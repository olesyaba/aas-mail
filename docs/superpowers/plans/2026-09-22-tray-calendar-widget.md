# Tray Calendar Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a macOS menu-bar presence to «Почта EAS.app» — time/next-meeting label, a popover to browse and expand today's events with a clickable join link, native event creation with live attendee availability, color-tagged local notifications, and Keychain-backed account passwords.

**Architecture:** A thin native SwiftUI layer (new files under `eas-bridge/app/`, compiled alongside the existing `main.swift`) that talks to the *already-running* local JSON API (`webapp.py`, unchanged except for two additions below) — no calendar/event logic is duplicated server-side, only a native presentation layer on top of `events/list`, `events/create`, `people/availability`, `/api/accounts`.

**Tech Stack:** Swift 5 (Cocoa + SwiftUI + Combine + UserNotifications), Python 3.12 (existing `webapp.py`/`bridge.py`), macOS `security` CLI for Keychain, `swiftc` (no SwiftPM/Xcode project — matches the existing single-script build).

**Spec:** `eas-bridge/docs/superpowers/specs/2026-09-22-tray-calendar-widget-design.md`

## Global Constraints

- Brand colors are fixed and must be reused exactly, not reinvented: Alfa-Bank `#501820`, Alfa-Seller `#003830` (same pair already used in `web/index.html`'s tabs and account-settings blocks).
- Notification color-coding is a text marker only (`🔴`/`🟢` in the title) — macOS does not allow a custom notification banner background; this was confirmed acceptable by the person who requested the feature.
- No new HTTP endpoints for auth: the tray reads the existing per-run CSRF token from a local file (`~/.config/eas-bridge/runtime_token`, mode 600), the same trust boundary already used for `config.json`.
- Account passwords must never be written to `config.json` after this plan lands; they live in the macOS Keychain (service `eas-bridge`, account = `main` or `seller`), read via `security find-generic-password`.
- This project has no existing automated test framework (no `pytest`, no `Package.swift`/XCTest — confirmed by inspection). Tasks that touch pure logic (no network/UI/system frameworks) get real automated tests, run as standalone scripts:
  - Python: `python3 -c "..."` one-liners that `assert` and `print("OK: ...")` on success.
  - Swift: **`swift File.swift test_file.swift` does NOT work** — confirmed by direct testing during Task 3 (the second file's top-level code is silently never run; `swift` script mode only executes the first argument). The corrected recipe: put the test code in a file literally named `main.swift` (Swift only allows top-level executable statements in a file with that exact name) inside a scratch directory, e.g. `eas-bridge/app/.swifttest/main.swift`, then compile it together with the real implementation file(s) via `swiftc`, and run the resulting binary — the files do not need to be copied into the same directory, only the driver file's basename must be `main.swift`:
    ```bash
    mkdir -p eas-bridge/app/.swifttest
    # write the test code to eas-bridge/app/.swifttest/main.swift (NOT test_X.swift)
    swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
    (cd eas-bridge/app/.swifttest && ./run)
    rm -rf eas-bridge/app/.swifttest
    ```
    For a RED step, the same `swiftc` invocation is expected to fail (the driver references a type/function that doesn't exist yet) — read the compiler's error for the missing symbol, same as before, just via `swiftc` instead of `swift`.
  Tasks that touch networking, AppKit/SwiftUI, Keychain, or UserNotifications are verified manually with exact commands/actions and an exact expected result — introducing URLProtocol mocking or an XCTest target for a single-user local tool is exactly the kind of ceremony the person asked to avoid ("легковесно").
- Every new Swift file must compile standalone with the rest of `app/*.swift` via `swiftc -O *.swift -o ...` (see Task 14) — do not introduce a second build system.

---

## Task 1: Runtime token file for the native layer

**Files:**
- Modify: `eas-bridge/webapp.py:34` (right after `TOKEN = secrets.token_urlsafe(24)`)

**Interfaces:**
- Consumes: `bridge.DATA_DIR` (existing, `bridge.py:16`)
- Produces: `~/.config/eas-bridge/runtime_token` (mode 600, plain text = the current `TOKEN`), read by `TrayAPIClient` in Task 8.

- [ ] **Step 1: Make the change**

In `webapp.py`, right after the `TOKEN = secrets.token_urlsafe(24)` line, add:

```python
TOKEN = secrets.token_urlsafe(24)
bridge.DATA_DIR.mkdir(parents=True, exist_ok=True)
(bridge.DATA_DIR / "runtime_token").write_text(TOKEN)
os.chmod(bridge.DATA_DIR / "runtime_token", 0o600)
```

- [ ] **Step 2: Verify**

```bash
cd eas-bridge && python3 -c "
import webapp, stat
p = webapp.bridge.DATA_DIR / 'runtime_token'
assert p.read_text() == webapp.TOKEN, 'token file does not match in-memory TOKEN'
mode = stat.S_IMODE(p.stat().st_mode)
assert mode == 0o600, f'expected 0o600, got {oct(mode)}'
print('OK: runtime_token written with correct content and permissions')
"
```

Expected: prints `OK: runtime_token written with correct content and permissions`. (Importing `webapp` does not start the HTTP server — that only happens under `if __name__ == "__main__":` in `main()` — so this is safe to run standalone.)

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add webapp.py && git commit -m "feat: write the per-run CSRF token to a local file for the native tray"
```

---

## Task 2: Keychain-backed account passwords

**Files:**
- Modify: `eas-bridge/webapp.py` (imports, `load_accounts` at line 66, `save_account_config` at line 106)

**Interfaces:**
- Produces: `get_password(account_id: str, cfg_password: str | None, service: str = "eas-bridge") -> str`, `_keychain_get(account_id, service) -> str | None`, `_keychain_set(account_id, password, service)` — used by `load_accounts` and `save_account_config`.

- [ ] **Step 1: Add `import subprocess`**

At the top of `webapp.py`, alongside the other stdlib imports (near `import secrets`), add `import subprocess`.

- [ ] **Step 2: Add the Keychain helpers**

Insert right after the existing `_write_config` function (currently ends around `webapp.py:103`):

```python
def _keychain_get(account_id: str, service: str = "eas-bridge") -> str | None:
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account_id, "-s", service, "-w"],
                            capture_output=True, text=True, check=True)
        pw = r.stdout.rstrip("\n")
        return pw or None
    except subprocess.CalledProcessError:
        return None


def _keychain_set(account_id: str, password: str, service: str = "eas-bridge"):
    subprocess.run(["/usr/bin/security", "add-generic-password", "-a", account_id, "-s", service,
                     "-w", password, "-U"], capture_output=True, text=True, check=True)


def get_password(account_id: str, cfg_password: str | None, service: str = "eas-bridge") -> str:
    """Keychain first; a plaintext password still in config.json (pre-Keychain
    accounts) is migrated in on first read and returned so the caller can use
    it right away without a second round-trip."""
    pw = _keychain_get(account_id, service)
    if pw:
        return pw
    if cfg_password:
        _keychain_set(account_id, cfg_password, service)
        return cfg_password
    return ""
```

- [ ] **Step 3: Verify the helpers in isolation**

Run against a throwaway Keychain service name so the real `main`/`seller` entries are never touched by this check:

```bash
cd eas-bridge && python3 -c "
import webapp
webapp._keychain_set('unittest-acct', 'hunter2', service='eas-bridge-test')
got = webapp._keychain_get('unittest-acct', service='eas-bridge-test')
assert got == 'hunter2', got
missing = webapp._keychain_get('does-not-exist', service='eas-bridge-test')
assert missing is None, missing
migrated = webapp.get_password('unittest-acct-2', 'plaintext-pw', service='eas-bridge-test')
assert migrated == 'plaintext-pw'
assert webapp._keychain_get('unittest-acct-2', service='eas-bridge-test') == 'plaintext-pw'
print('OK: keychain round-trip + migration fallback both work')
"
security delete-generic-password -a unittest-acct -s eas-bridge-test 2>/dev/null
security delete-generic-password -a unittest-acct-2 -s eas-bridge-test 2>/dev/null
```

Expected: prints `OK: keychain round-trip + migration fallback both work`, then the two `security delete-generic-password` cleanup calls run silently (they may print nothing or a benign "not found" if a prior run already cleaned up — either is fine).

- [ ] **Step 4: Wire it into `load_accounts`**

Replace the current body of `load_accounts` (`webapp.py:66-78`):

```python
def load_accounts(cfg: dict) -> dict[str, Acct]:
    import hashlib
    import socket
    main_cfg = {**cfg, "password": get_password("main", cfg.get("password")), "state_name": "state.json"}
    out = {"main": Acct("main", "Alfa-Bank", main_cfg, False)}
    s2 = cfg.get("second")
    if s2:
        dev = s2.get("device_id") or hashlib.sha256(
            f"eas-bridge|{s2['username']}|{s2['url']}|{socket.gethostname()}".encode()).hexdigest()[:32].upper()
        out["seller"] = Acct("seller", s2.get("name", "Seller"), {
            "username": s2["username"], "password": get_password("seller", s2.get("password")), "url": s2["url"], "device_id": dev,
            "email": s2.get("email", s2["username"]), "state_name": "state-seller.json",
            "imap_window_filter": cfg.get("imap_window_filter", 5)}, True)
    return out
```

- [ ] **Step 5: Wire it into `save_account_config`**

Replace the body of `save_account_config` (`webapp.py:106-157`) with:

```python
def save_account_config(p: dict) -> dict:
    """Update config.json for one account and reconnect it immediately, so
    the caller learns right away whether the new credentials actually work."""
    import hashlib
    import socket
    from outlook_activesync_mcp.commands import settings as settings_cmd
    target = p.get("target")
    if target not in ("main", "seller"):
        return {"ok": False, "error": "bad_request", "message": "неизвестный аккаунт"}
    url, username, email = (p.get("url") or "").strip(), (p.get("username") or "").strip(), (p.get("email") or "").strip()
    password = p.get("password") or ""
    if not url or not username or not email:
        return {"ok": False, "error": "bad_request", "message": "заполните сервер, логин и email"}
    cfg = json.loads(bridge.CONF_PATH.read_text())
    if target == "main":
        if not password:
            password = get_password("main", cfg.get("password"))
        cfg.update(url=url, username=username, email=email)
        cfg.pop("password", None)
        _keychain_set("main", password)
        merged = {"url": url, "username": username, "email": email, "password": password,
                  "device_id": cfg["device_id"], "state_name": "state.json",
                  "imap_window_filter": cfg.get("imap_window_filter", 5)}
        aid, name, green = "main", "Alfa-Bank", False
    else:
        s2 = cfg.get("second") or {}
        if not password:
            password = get_password("seller", s2.get("password"))
        if not password:
            return {"ok": False, "error": "bad_request", "message": "укажите пароль для нового аккаунта"}
        name = (p.get("name") or s2.get("name") or "Alfa-Seller").strip() or "Alfa-Seller"
        dev = s2.get("device_id") or hashlib.sha256(
            f"eas-bridge|{username}|{url}|{socket.gethostname()}".encode()).hexdigest()[:32].upper()
        _keychain_set("seller", password)
        cfg["second"] = {"name": name, "url": url, "username": username, "email": email, "device_id": dev}
        merged = {"username": username, "password": password, "url": url, "device_id": dev,
                  "email": email, "state_name": "state-seller.json",
                  "imap_window_filter": cfg.get("imap_window_filter", 5)}
        aid, name, green = "seller", name, True
    _write_config(cfg)
    backend = bridge.EasBackend(merged)
    login_ok, message = True, None
    try:
        with backend.lock:
            settings_cmd.handle(backend.client, "status")
    except Exception as e:  # noqa: BLE001
        login_ok, message = False, str(e)
    new_acct = Acct(aid, name, merged, green)
    new_acct.backend = backend
    ACCTS[aid] = new_acct
    if login_ok:
        threading.Thread(target=backend.ensure_identity, daemon=True).start()
        cal_refresh_bg(new_acct)
    return {"ok": True, "login_ok": login_ok, "message": message}
```

- [ ] **Step 6: Syntax check**

```bash
cd eas-bridge && python3 -m py_compile webapp.py && echo OK
```

Expected: `OK`.

- [ ] **Step 7: Manual end-to-end check (do this once the app is running — Task 14's build)**

1. Quit and relaunch «Почта EAS.app» so it picks up the new code.
2. Confirm both accounts still connect (folders load in both tabs) — proves `get_password`'s migration path works for the *existing* plaintext-password `config.json`.
3. Open ⚙ Настройки, change nothing, click «Сохранить».
4. Run: `python3 -c "import json; c = json.load(open('$HOME/.config/eas-bridge/config.json')); print('password' in c, 'password' in c.get('second', {}))"`
   Expected: `False False` — the plaintext password is gone from the file after the first save through the new code.
5. Run: `security find-generic-password -a main -s eas-bridge -w >/dev/null && echo "main OK"` and the same with `-a seller` — both should print `... OK`, confirming the passwords now live in Keychain.

- [ ] **Step 8: Commit**

```bash
cd eas-bridge && git add webapp.py && git commit -m "feat: store account passwords in the macOS Keychain instead of config.json"
```

---

## Task 3: TrayModels.swift — event model + JSON decoding

**Files:**
- Create: `eas-bridge/app/TrayModels.swift`
- Test (throwaway, deleted after Step 4): `eas-bridge/app/test_traymodels.swift`

**Interfaces:**
- Produces: `TrayAttendee`, `TrayEvent` (fields: `itemId`, `subject`, `startISO`, `end`, `location`, `isAllDay`, `busyStatus`, `attendees`, `body`, plus mutable `accountId`/`accountName`/`accountColorHex` tags with `""` defaults, plus computed `startDate`/`endDate`), `TrayEventListResponse` (`ok`, `items: [TrayEvent]`) — all consumed by Tasks 4, 5, 6, 7, 8, 9, 11, 12.

- [ ] **Step 1: Write the failing test**

Create `eas-bridge/app/test_traymodels.swift`:

```swift
import Foundation

let sampleJSON = """
{"ok": true, "items": [
  {"item_id": "abc123", "subject": "Daily ELK", "start_iso": "2026-09-22T07:30:00Z",
   "end": "2026-09-22 11:00", "location": "https://alfabank.ktalk.ru/i4iy08xa43l3",
   "is_all_day": false, "busy_status": "busy", "attendees": [{"name": "A", "address": "a@x.ru"}],
   "body": null}
]}
""".data(using: .utf8)!

let decoded = try! JSONDecoder().decode(TrayEventListResponse.self, from: sampleJSON)
guard decoded.items.count == 1 else { fatalError("expected 1 item, got \(decoded.items.count)") }
let e = decoded.items[0]
guard e.itemId == "abc123" else { fatalError("item_id mismatch") }
guard e.subject == "Daily ELK" else { fatalError("subject mismatch") }
guard e.startDate != nil else { fatalError("startDate failed to parse from start_iso") }
guard e.endDate != nil else { fatalError("endDate failed to parse from end") }
guard e.endDate! > e.startDate! else { fatalError("end should be after start") }
guard e.accountId == "" else { fatalError("accountId should default to empty until tagged") }
print("OK: TrayModels decode + date parsing")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd eas-bridge/app && swift test_traymodels.swift
```

Expected: compile error — `cannot find type 'TrayEventListResponse' in scope` (or similar), because `TrayModels.swift` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `eas-bridge/app/TrayModels.swift`:

```swift
import Foundation

struct TrayAttendee: Decodable {
    let name: String?
    let address: String?
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

    var id: String { itemId }

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
        f.timeZone = TimeZone.current
        return f
    }()

    var startDate: Date? { Self.isoFormatter.date(from: startISO) }
    var endDate: Date? {
        guard !end.isEmpty else { return startDate }
        return Self.localFormatter.date(from: end)
    }
}

struct TrayEventListResponse: Decodable {
    let ok: Bool
    let items: [TrayEvent]
}
```

- [ ] **Step 4: Run it to verify it passes, then remove the throwaway test file**

This is a *multi*-file invocation (implementation + test) — `swift TrayModels.swift test_traymodels.swift` does not work (confirmed by direct testing: `swift` script mode only runs the first file's top-level code). Use the `main.swift`-named scratch-dir recipe from Global Constraints instead:

```bash
mkdir -p eas-bridge/app/.swifttest
cp eas-bridge/app/test_traymodels.swift eas-bridge/app/.swifttest/main.swift
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `OK: TrayModels decode + date parsing`.

```bash
cd eas-bridge/app && rm test_traymodels.swift && rm -rf .swifttest
```

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/TrayModels.swift && git commit -m "feat: add TrayEvent model for the native tray"
```

---

## Task 4: TrayEventMerge.swift — merge, tag, sort, filter past events

**Files:**
- Create: `eas-bridge/app/TrayEventMerge.swift`
- Test (throwaway): `eas-bridge/app/test_traymerge.swift`

**Interfaces:**
- Consumes: `TrayEvent` (Task 3)
- Produces: `TrayEventMerge.merge(main: [TrayEvent], mainName: String, mainColor: String, seller: [TrayEvent], sellerName: String, sellerColor: String, now: Date) -> [TrayEvent]` — sorted ascending by start, only events whose `endDate` is after `now`, each tagged with its account. Used by Task 9.

- [ ] **Step 1: Write the failing test**

Create `eas-bridge/app/.swifttest/main.swift` (directory doesn't exist yet — create it first):

```swift
import Foundation

func mkEvent(id: String, startISO: String, end: String) -> TrayEvent {
    let json = """
    {"item_id": "\(id)", "subject": "S-\(id)", "start_iso": "\(startISO)", "end": "\(end)",
     "location": null, "is_all_day": false, "busy_status": "busy", "attendees": [], "body": null}
    """.data(using: .utf8)!
    return try! JSONDecoder().decode(TrayEvent.self, from: json)
}

let now = ISO8601DateFormatter().date(from: "2026-09-22T08:00:00Z")!
let mainEvents = [mkEvent(id: "m1", startISO: "2026-09-22T10:00:00Z", end: "2026-09-22 13:03"),
                  mkEvent(id: "m0-past", startISO: "2026-09-22T06:00:00Z", end: "2026-09-22 09:00")]
let sellerEvents = [mkEvent(id: "s1", startISO: "2026-09-22T09:00:00Z", end: "2026-09-22 12:00")]

let merged = TrayEventMerge.merge(main: mainEvents, mainName: "Alfa-Bank", mainColor: "#501820",
                                   seller: sellerEvents, sellerName: "Alfa-Seller", sellerColor: "#003830", now: now)
guard merged.count == 2 else { fatalError("expected 2 upcoming events (past one filtered), got \(merged.count)") }
guard merged[0].itemId == "s1" else { fatalError("expected s1 first (earlier start), got \(merged[0].itemId)") }
guard merged[0].accountId == "seller" && merged[0].accountColorHex == "#003830" else { fatalError("seller tagging wrong") }
guard merged[1].itemId == "m1" && merged[1].accountId == "main" else { fatalError("main tagging wrong") }
print("OK: TrayEventMerge tags, merges, filters past events, sorts by start")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
```

Expected: compile error, `cannot find 'TrayEventMerge' in scope`.

- [ ] **Step 3: Write the implementation**

Create `eas-bridge/app/TrayEventMerge.swift`:

```swift
import Foundation

enum TrayEventMerge {
    /// Tags each event with its source account, merges both lists, drops
    /// anything already over (endDate <= now), and sorts by start time.
    static func merge(main: [TrayEvent], mainName: String, mainColor: String,
                       seller: [TrayEvent], sellerName: String, sellerColor: String,
                       now: Date) -> [TrayEvent] {
        func tag(_ events: [TrayEvent], id: String, name: String, color: String) -> [TrayEvent] {
            events.map { e in
                var e2 = e
                e2.accountId = id; e2.accountName = name; e2.accountColorHex = color
                return e2
            }
        }
        let all = tag(main, id: "main", name: mainName, color: mainColor)
            + tag(seller, id: "seller", name: sellerName, color: sellerColor)
        return all
            .filter { ($0.endDate ?? $0.startDate ?? .distantPast) > now }
            .sorted { ($0.startDate ?? .distantPast) < ($1.startDate ?? .distantPast) }
    }
}
```

- [ ] **Step 4: Run it to verify it passes, then remove the scratch test directory**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/TrayEventMerge.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `OK: TrayEventMerge tags, merges, filters past events, sorts by start`.

```bash
rm -rf eas-bridge/app/.swifttest
```

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/TrayEventMerge.swift && git commit -m "feat: merge and sort events from both accounts for the tray"
```

---

## Task 5: TrayLabelFormatter.swift — menu bar label text

**Files:**
- Create: `eas-bridge/app/TrayLabelFormatter.swift`
- Test (throwaway): `eas-bridge/app/test_traylabel.swift`

**Interfaces:**
- Consumes: `TrayEvent` (Task 3) — expects an already-merged/sorted/filtered list (Task 4's output).
- Produces: `TrayLabelFormatter.label(events: [TrayEvent], now: Date) -> String` — used by Task 10.

- [ ] **Step 1: Write the failing test**

Create `eas-bridge/app/.swifttest/main.swift` (directory doesn't exist yet — create it first):

```swift
import Foundation

func mkEvent(id: String, startISO: String, end: String) -> TrayEvent {
    let json = """
    {"item_id": "\(id)", "subject": "\(id)", "start_iso": "\(startISO)", "end": "\(end)",
     "location": null, "is_all_day": false, "busy_status": "busy", "attendees": [], "body": null}
    """.data(using: .utf8)!
    return try! JSONDecoder().decode(TrayEvent.self, from: json)
}

let now = ISO8601DateFormatter().date(from: "2026-09-22T08:00:00Z")!

guard TrayLabelFormatter.label(events: [], now: now) == "📅" else { fatalError("empty case should show calendar glyph") }

let soon = mkEvent(id: "Daily ELK", startISO: "2026-09-22T08:12:00Z", end: "2026-09-22 11:42")
let soonLabel = TrayLabelFormatter.label(events: [soon], now: now)
guard soonLabel == "Daily ELK · через 12 мин" else { fatalError("expected 12-minute countdown, got \(soonLabel)") }

let ongoing = mkEvent(id: "Standup", startISO: "2026-09-22T07:50:00Z", end: "2026-09-22 11:15")
let ongoingLabel = TrayLabelFormatter.label(events: [ongoing], now: now)
guard ongoingLabel == "▶ Standup" else { fatalError("expected in-progress marker, got \(ongoingLabel)") }

let later = mkEvent(id: "Later", startISO: "2026-09-22T09:30:00Z", end: "2026-09-22 13:00")
let laterLabel = TrayLabelFormatter.label(events: [later], now: now)
guard laterLabel == "Later · через 1 ч 30 мин" else { fatalError("expected hour+minutes countdown, got \(laterLabel)") }

print("OK: TrayLabelFormatter countdown/label formatting")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
```

Expected: compile error, `cannot find 'TrayLabelFormatter' in scope`.

- [ ] **Step 3: Write the implementation**

Create `eas-bridge/app/TrayLabelFormatter.swift`:

```swift
import Foundation

enum TrayLabelFormatter {
    /// `events` must already be sorted by start time and filtered to
    /// "still relevant" (endDate > now) — TrayEventMerge does exactly that.
    static func label(events: [TrayEvent], now: Date) -> String {
        guard let first = events.first, let start = first.startDate else { return "📅" }
        let end = first.endDate ?? start
        if start <= now && now < end {
            return "▶ \(first.subject)"
        }
        let minutes = Int(ceil(start.timeIntervalSince(now) / 60))
        if minutes <= 0 { return "▶ \(first.subject)" }
        if minutes < 60 {
            return "\(first.subject) · через \(minutes) мин"
        }
        let hours = minutes / 60, rem = minutes % 60
        let hoursPart = "\(hours) ч" + (rem > 0 ? " \(rem) мин" : "")
        return "\(first.subject) · через \(hoursPart)"
    }
}
```

- [ ] **Step 4: Run it to verify it passes, then remove the scratch test directory**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/TrayLabelFormatter.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `OK: TrayLabelFormatter countdown/label formatting`.

```bash
rm -rf eas-bridge/app/.swifttest
```

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/TrayLabelFormatter.swift && git commit -m "feat: format the menu bar label with a live countdown"
```

---

## Task 6: TrayAvailability.swift — freebusy code → status/color

**Files:**
- Create: `eas-bridge/app/TrayAvailability.swift`
- Test (throwaway): `eas-bridge/app/test_trayavail.swift`

**Interfaces:**
- Produces: `AvailabilityStatus` (`.free`, `.tentative`, `.busy`, `.outOfOffice`, `.unknown`, each with `.label: String` and `.marker: String`), `TrayAvailability.status(freebusy: String?) -> AvailabilityStatus` — used by Task 12.

- [ ] **Step 1: Write the failing test**

Create `eas-bridge/app/test_trayavail.swift` (single file — this one still runs fine with plain `swift`, since only *multi*-file invocations need the `.swifttest/main.swift` treatment described in Global Constraints):

```swift
import Foundation

guard TrayAvailability.status(freebusy: "0000") == .free else { fatalError("all-free window should read free") }
guard TrayAvailability.status(freebusy: "0002") == .busy else { fatalError("any busy slot should win over free") }
guard TrayAvailability.status(freebusy: "0013") == .outOfOffice else { fatalError("oof should win over tentative/free") }
guard TrayAvailability.status(freebusy: "0001") == .tentative else { fatalError("tentative should win over free") }
guard TrayAvailability.status(freebusy: "") == .unknown else { fatalError("empty string should be unknown") }
guard TrayAvailability.status(freebusy: nil) == .unknown else { fatalError("nil should be unknown") }
guard TrayAvailability.status(freebusy: "0002").marker == "⛔" else { fatalError("busy marker mismatch") }
guard TrayAvailability.status(freebusy: "0000").marker == "✅" else { fatalError("free marker mismatch") }
print("OK: TrayAvailability freebusy → status mapping matches web UI priority")
```

This mirrors the priority order and grouping already used by the web UI at `web/index.html:836-839` (`['2', '3', '1', '0']` search order, `L` table) — do not change the priority order, it's the existing, already-shipped behavior.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd eas-bridge/app && swift test_trayavail.swift
```

Expected: `cannot find 'TrayAvailability' in scope`.

- [ ] **Step 3: Write the implementation**

Create `eas-bridge/app/TrayAvailability.swift`:

```swift
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
```

- [ ] **Step 4: Run it to verify it passes, then remove the throwaway test file**

`TrayAvailability.swift` has no dependency on `TrayModels.swift`, but this is still a *multi*-file invocation (implementation + test), so it needs the `main.swift`-named scratch-dir treatment, not plain `swift`:

```bash
mkdir -p eas-bridge/app/.swifttest
cp eas-bridge/app/test_trayavail.swift eas-bridge/app/.swifttest/main.swift
swiftc eas-bridge/app/TrayAvailability.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `OK: TrayAvailability freebusy → status mapping matches web UI priority`.

```bash
cd eas-bridge/app && rm test_trayavail.swift && rm -rf .swifttest
```

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/TrayAvailability.swift && git commit -m "feat: add freebusy-code to availability-status mapping for the native form"
```

---

## Task 7: TrayNotificationPlan.swift — pure schedule/cancel diff

**Files:**
- Create: `eas-bridge/app/TrayNotificationPlan.swift`
- Test (throwaway): `eas-bridge/app/test_traynotifplan.swift`

**Interfaces:**
- Consumes: `TrayEvent` (Task 3)
- Produces: `TrayNotificationRequest` (`identifier`, `title`, `body`, `fireDate`), `TrayNotificationPlan.plan(events: [TrayEvent], now: Date, leadMinutes: Int = 5, previouslyScheduledIds: Set<String>) -> (toSchedule: [TrayNotificationRequest], toCancel: [String])` — used by Task 13.

- [ ] **Step 1: Write the failing test**

Create `eas-bridge/app/.swifttest/main.swift` (directory doesn't exist yet — create it first):

```swift
import Foundation

func mkEvent(id: String, startISO: String, end: String, accountId: String, accountName: String) -> TrayEvent {
    let json = """
    {"item_id": "\(id)", "subject": "S-\(id)", "start_iso": "\(startISO)", "end": "\(end)",
     "location": null, "is_all_day": false, "busy_status": "busy", "attendees": [], "body": null}
    """.data(using: .utf8)!
    var e = try! JSONDecoder().decode(TrayEvent.self, from: json)
    e.accountId = accountId; e.accountName = accountName
    return e
}

let now = ISO8601DateFormatter().date(from: "2026-09-22T08:00:00Z")!
let soon = mkEvent(id: "e1", startISO: "2026-09-22T08:20:00Z", end: "2026-09-22 11:50", accountId: "main", accountName: "Alfa-Bank")
let tooClose = mkEvent(id: "e2", startISO: "2026-09-22T08:03:00Z", end: "2026-09-22 11:30", accountId: "seller", accountName: "Alfa-Seller")

let (scheduled, cancelled) = TrayNotificationPlan.plan(events: [soon, tooClose], now: now, leadMinutes: 5, previouslyScheduledIds: ["eas-bridge-event-stale"])
guard scheduled.count == 1 else { fatalError("expected only e1's reminder (e2's lead time already passed), got \(scheduled.count)") }
guard scheduled[0].identifier == "eas-bridge-event-e1" else { fatalError("wrong identifier: \(scheduled[0].identifier)") }
guard scheduled[0].title == "🔴 Alfa-Bank" else { fatalError("expected red marker for main account, got \(scheduled[0].title)") }
guard cancelled == ["eas-bridge-event-stale"] else { fatalError("stale reminder should be cancelled, got \(cancelled)") }

let (scheduled2, _) = TrayNotificationPlan.plan(events: [tooClose], now: now, leadMinutes: 5, previouslyScheduledIds: [])
guard scheduled2.isEmpty else { fatalError("event starting in under leadMinutes should not get a reminder") }

print("OK: TrayNotificationPlan schedules future reminders, cancels stale ones, marks account color")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
```

Expected: compile error, `cannot find 'TrayNotificationPlan' in scope`.

- [ ] **Step 3: Write the implementation**

Create `eas-bridge/app/TrayNotificationPlan.swift`:

```swift
import Foundation

struct TrayNotificationRequest: Equatable {
    let identifier: String
    let title: String
    let body: String
    let fireDate: Date
}

enum TrayNotificationPlan {
    /// Given the current merged event list and "now", returns the reminder
    /// requests that should be scheduled (start - leadMinutes, only if still
    /// in the future) plus the identifiers of previously-scheduled reminders
    /// that are no longer relevant (event dropped out of the list) and must
    /// be cancelled first, to avoid duplicate/stale notifications piling up
    /// across refreshes.
    static func plan(events: [TrayEvent], now: Date, leadMinutes: Int = 5,
                      previouslyScheduledIds: Set<String>) -> (toSchedule: [TrayNotificationRequest], toCancel: [String]) {
        var toSchedule: [TrayNotificationRequest] = []
        var stillWanted: Set<String> = []
        for e in events {
            guard let start = e.startDate else { continue }
            let fireDate = start.addingTimeInterval(-Double(leadMinutes) * 60)
            guard fireDate > now else { continue }
            let id = "eas-bridge-event-\(e.itemId)"
            stillWanted.insert(id)
            let marker = e.accountId == "seller" ? "🟢" : "🔴"
            toSchedule.append(TrayNotificationRequest(
                identifier: id,
                title: "\(marker) \(e.accountName)",
                body: "\(e.subject) через \(leadMinutes) мин",
                fireDate: fireDate))
        }
        let toCancel = previouslyScheduledIds.subtracting(stillWanted)
        return (toSchedule, Array(toCancel))
    }
}
```

- [ ] **Step 4: Run it to verify it passes, then remove the scratch test directory**

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/TrayNotificationPlan.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `OK: TrayNotificationPlan schedules future reminders, cancels stale ones, marks account color`.

```bash
rm -rf eas-bridge/app/.swifttest
```

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/TrayNotificationPlan.swift && git commit -m "feat: add pure schedule/cancel diff logic for meeting reminders"
```

---

## Task 8: TrayAPIClient.swift — HTTP client for the local backend

**Files:**
- Create: `eas-bridge/app/TrayAPIClient.swift`

**Interfaces:**
- Consumes: `TrayEvent`, `TrayEventListResponse` (Task 3); reads `~/.config/eas-bridge/runtime_token` (Task 1).
- Produces: `TrayAPIClient` with `listEvents(acct:start:end:) async throws -> [TrayEvent]`, `createEvent(acct:subject:location:body:start:end:attendees:) async throws`, `availability(acct:who:start:end:) async throws -> [TrayAvailabilityItem]`, `accountIds() async throws -> Set<String>`. Used by Task 9 and Task 12.

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/TrayAPIClient.swift`:

```swift
import Foundation

struct TrayAvailabilityItem: Decodable {
    let name: String?
    let address: String?
    let freebusy: String?
}

struct TrayAvailabilityResponse: Decodable {
    let ok: Bool
    let items: [TrayAvailabilityItem]?
}

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
        return try JSONDecoder().decode(TrayEventListResponse.self, from: data).items
    }

    func createEvent(acct: String, subject: String, location: String, body: String,
                      start: String, end: String, attendees: [String]) async throws {
        var payload: [String: Any] = ["acct": acct, "action": "create", "subject": subject,
                                       "location": location, "body": body, "start": start, "end": end]
        if !attendees.isEmpty { payload["attendees"] = attendees }
        let data = try await post("/api/events", payload)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        if (obj?["ok"] as? Bool) != true {
            throw TrayAPIError.serverError(obj?["message"] as? String ?? "unknown error")
        }
    }

    func availability(acct: String, who: [String], start: String, end: String) async throws -> [TrayAvailabilityItem] {
        let data = try await post("/api/people", ["acct": acct, "action": "availability", "who": who, "start": start, "end": end])
        let decoded = try JSONDecoder().decode(TrayAvailabilityResponse.self, from: data)
        guard decoded.ok else { throw TrayAPIError.serverError("availability lookup failed") }
        return decoded.items ?? []
    }

    func accountIds() async throws -> Set<String> {
        let data = try await post("/api/accounts", [:])
        let decoded = try JSONDecoder().decode(TrayAccountsResponse.self, from: data)
        return Set(decoded.accounts.map { $0.id })
    }
}
```

- [ ] **Step 2: Manual verification against the real running server**

The app must already be running (or start it: `cd eas-bridge/app && bash build_app.sh && open ~/Applications/Почта\ EAS.app`) so `webapp.py` has written `~/.config/eas-bridge/runtime_token` (Task 1).

Create a throwaway script — must be named exactly `main.swift` (see Global Constraints on Swift multi-file verification) in a scratch dir:

```bash
mkdir -p eas-bridge/app/.swifttest
```

`eas-bridge/app/.swifttest/main.swift`:

```swift
import Foundation
let client = TrayAPIClient()
Task {
    do {
        let ids = try await client.accountIds()
        print("accounts: \(ids)")
        let events = try await client.listEvents(acct: "main", start: "2026-09-22", end: "2026-09-23")
        print("OK: fetched \(events.count) events for main account")
    } catch { print("FAILED: \(error)") }
    exit(0)
}
RunLoop.main.run(until: Date().addingTimeInterval(5))
```

Run:

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/TrayAPIClient.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `accounts: ["main", "seller"]` (or just `["main"]` if Alfa-Seller isn't configured), then `OK: fetched N events for main account` with N ≥ 0. Then delete the throwaway script:

```bash
rm -rf eas-bridge/app/.swifttest
```

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/TrayAPIClient.swift && git commit -m "feat: add native HTTP client for the local eas-mail JSON API"
```

---

## Task 9: TrayDataStore.swift — polling + merge + today/tomorrow fallback

**Files:**
- Create: `eas-bridge/app/TrayDataStore.swift`

**Interfaces:**
- Consumes: `TrayAPIClient` (Task 8), `TrayEventMerge.merge` (Task 4)
- Produces: `TrayDataStore` (`ObservableObject`, `@Published var events: [TrayEvent]`, `@Published var lastError: String?`, `private(set) var hasSeller: Bool`, `func start()`, `func stop()`, `func refresh() async`). Used by Task 10, Task 11, Task 12.

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/TrayDataStore.swift`:

```swift
import Foundation

@MainActor
final class TrayDataStore: ObservableObject {
    @Published var events: [TrayEvent] = []
    @Published var lastError: String?
    private(set) var hasSeller = false

    static let mainColor = "#501820"
    static let sellerColor = "#003830"

    private let client = TrayAPIClient()
    private var timer: Timer?
    private var accountsResolved = false

    func start() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
        Task { await refresh() }
    }

    func stop() { timer?.invalidate(); timer = nil }

    func refresh() async {
        do {
            if !accountsResolved {
                hasSeller = try await client.accountIds().contains("seller")
                accountsResolved = true
            }
            let cal = Calendar.current
            let now = Date()
            let todayStart = cal.startOfDay(for: now)
            let todayEnd = cal.date(byAdding: .day, value: 1, to: todayStart)!
            let df = ISO8601DateFormatter(); df.formatOptions = [.withFullDate]

            async let mainToday = fetchAccount("main", start: todayStart, end: todayEnd, formatter: df)
            async let sellerToday = fetchAccount("seller", start: todayStart, end: todayEnd, formatter: df)
            var merged = TrayEventMerge.merge(main: try await mainToday, mainName: "Alfa-Bank", mainColor: Self.mainColor,
                                               seller: try await sellerToday, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor, now: now)

            if merged.isEmpty {
                let tomorrowStart = todayEnd
                let tomorrowEnd = cal.date(byAdding: .day, value: 1, to: tomorrowStart)!
                async let mainTomorrow = fetchAccount("main", start: tomorrowStart, end: tomorrowEnd, formatter: df)
                async let sellerTomorrow = fetchAccount("seller", start: tomorrowStart, end: tomorrowEnd, formatter: df)
                merged = TrayEventMerge.merge(main: try await mainTomorrow, mainName: "Alfa-Bank", mainColor: Self.mainColor,
                                               seller: try await sellerTomorrow, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor,
                                               now: todayEnd.addingTimeInterval(-1))
            }
            events = merged
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    private func fetchAccount(_ acct: String, start: Date, end: Date, formatter: ISO8601DateFormatter) async throws -> [TrayEvent] {
        if acct == "seller" && !hasSeller { return [] }
        return try await client.listEvents(acct: acct, start: formatter.string(from: start), end: formatter.string(from: end))
    }
}
```

- [ ] **Step 2: Manual verification**

Create a throwaway script — must be named exactly `main.swift` (see Global Constraints on Swift multi-file verification) in a scratch dir (app must be running, same precondition as Task 8):

```bash
mkdir -p eas-bridge/app/.swifttest
```

`eas-bridge/app/.swifttest/main.swift`:

```swift
import Foundation
let store = TrayDataStore()
Task {
    await store.refresh()
    print("hasSeller=\(store.hasSeller) events=\(store.events.count) error=\(store.lastError ?? "none")")
    exit(0)
}
RunLoop.main.run(until: Date().addingTimeInterval(10))
```

Run:

```bash
swiftc eas-bridge/app/TrayModels.swift eas-bridge/app/TrayEventMerge.swift eas-bridge/app/TrayAPIClient.swift eas-bridge/app/TrayDataStore.swift eas-bridge/app/.swifttest/main.swift -o eas-bridge/app/.swifttest/run
(cd eas-bridge/app/.swifttest && ./run)
```

Expected: `hasSeller=true events=N error=none` (N ≥ 0 — 0 is fine if there's genuinely nothing left today or tomorrow). Then:

```bash
rm -rf eas-bridge/app/.swifttest
```

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/TrayDataStore.swift && git commit -m "feat: add polling data store that merges both calendars for the tray"
```

---

## Task 10: TrayStatusController.swift — NSStatusItem + popover host

**Files:**
- Create: `eas-bridge/app/TrayStatusController.swift`

**Interfaces:**
- Consumes: `TrayDataStore` (Task 9), `TrayLabelFormatter.label` (Task 5), `TrayPopoverView` (Task 11 — written next, but referenced here; Task 11 must land before this compiles cleanly, which the task order already guarantees), `EventCreateView` (Task 12), `NotificationScheduler` (Task 13).
- Produces: `TrayStatusController` (no-arg `init()`), instantiated once by `main.swift` (Task 14).

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/TrayStatusController.swift`:

```swift
import Cocoa
import SwiftUI
import Combine

@MainActor final class TrayStatusController: NSObject {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let popover = NSPopover()
    private let store = TrayDataStore()
    private var labelTimer: Timer?
    private var cancellables = Set<AnyCancellable>()
    private let notifications = NotificationScheduler()
    private var eventCreateWindow: NSWindow?

    override init() {
        super.init()
        statusItem.button?.title = "📅"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePopover)
        popover.behavior = .transient
        popover.contentSize = NSSize(width: 320, height: 420)
        popover.contentViewController = NSHostingController(
            rootView: TrayPopoverView(store: store, onCreate: { [weak self] in self?.openCreate() }))
        store.$events
            .sink { [weak self] events in self?.notifications.reschedule(for: events) }
            .store(in: &cancellables)
        store.start()
        labelTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refreshLabel() }
        notifications.requestAuthorizationIfNeeded()
    }

    private func refreshLabel() {
        statusItem.button?.title = TrayLabelFormatter.label(events: store.events, now: Date())
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown { popover.performClose(nil) }
        else { popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY) }
    }

    private func openCreate() {
        let hosting = NSHostingController(rootView: EventCreateView(hasSeller: store.hasSeller) { [weak self] in
            self?.eventCreateWindow?.close()
            Task { await self?.store.refresh() }
        })
        let window = NSWindow(contentViewController: hosting)
        window.title = "Новое событие"
        window.styleMask = [.titled, .closable]
        popover.performClose(nil)
        window.center()
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        eventCreateWindow = window
    }
}
```

- [ ] **Step 2: Verify it compiles together with everything so far**

This can't run standalone (it needs `TrayPopoverView` and `EventCreateView`, Tasks 11-12) — just confirm no obvious typos by re-reading the file against the `Interfaces` block above. Full compile verification happens in Task 14.

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/TrayStatusController.swift && git commit -m "feat: add NSStatusItem controller hosting the tray popover"
```

---

## Task 11: TrayPopoverView.swift — event list + expand + join link

**Files:**
- Create: `eas-bridge/app/TrayPopoverView.swift`

**Interfaces:**
- Consumes: `TrayDataStore` (Task 9), `TrayEvent` (Task 3)
- Produces: `TrayPopoverView(store: TrayDataStore, onCreate: () -> Void)` — a SwiftUI `View`. Used by Task 10.

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/TrayPopoverView.swift`:

```swift
import SwiftUI

struct TrayPopoverView: View {
    @ObservedObject var store: TrayDataStore
    var onCreate: () -> Void
    @State private var expandedId: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(headerText).font(.headline).padding(12)
            Divider()
            if store.events.isEmpty {
                Text("Нет встреч").foregroundColor(.secondary).padding(12)
                Spacer()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(store.events) { event in
                            EventRow(event: event, expanded: expandedId == event.id)
                                .onTapGesture { expandedId = expandedId == event.id ? nil : event.id }
                            Divider()
                        }
                    }
                }
            }
            Divider()
            Button("Создать", action: onCreate).padding(10)
        }
        .frame(width: 320, height: 420)
    }

    private var headerText: String {
        guard let first = store.events.first, let start = first.startDate else { return "Сегодня" }
        let cal = Calendar.current
        if cal.isDateInToday(start) { return "Сегодня" }
        if cal.isDateInTomorrow(start) { return "Завтра" }
        return ""
    }
}

private struct EventRow: View {
    let event: TrayEvent
    let expanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(Color(hex: event.accountColorHex)).frame(width: 8, height: 8)
                Text(timeText).font(.caption).foregroundColor(.secondary).frame(width: 40, alignment: .leading)
                Text(event.subject).lineLimit(1)
            }
            if expanded {
                VStack(alignment: .leading, spacing: 4) {
                    if let start = event.startDate, let end = event.endDate {
                        Text("\(start.formatted(date: .omitted, time: .shortened)) – \(end.formatted(date: .omitted, time: .shortened))")
                            .font(.caption)
                    }
                    if let location = event.location, !location.isEmpty {
                        if let url = URL(string: location), url.scheme?.hasPrefix("http") == true {
                            Link(location, destination: url).font(.caption).lineLimit(1)
                        } else {
                            Text(location).font(.caption).lineLimit(2)
                        }
                    }
                    if let attendees = event.attendees, !attendees.isEmpty {
                        Text("Участников: \(attendees.count)").font(.caption).foregroundColor(.secondary)
                    }
                }
                .padding(.leading, 16)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .contentShape(Rectangle())
    }

    private var timeText: String {
        guard let start = event.startDate else { return "" }
        return start.formatted(date: .omitted, time: .shortened)
    }
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
```

- [ ] **Step 2: Verify (deferred to Task 14's full build)**

This view needs `TrayStatusController` (Task 10) to host it and the full app to build — visual verification happens in Task 14's manual checklist.

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/TrayPopoverView.swift && git commit -m "feat: add tray popover event list with expand and join-link support"
```

---

## Task 12: EventCreateView.swift — native creation form + availability check

**Files:**
- Create: `eas-bridge/app/EventCreateView.swift`

**Interfaces:**
- Consumes: `TrayAPIClient` (Task 8), `TrayAvailability.status` (Task 6)
- Produces: `EventCreateView(hasSeller: Bool, onDone: @escaping () -> Void)` — a SwiftUI `View`. Used by Task 10.

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/EventCreateView.swift`:

```swift
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
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd'T'HH:mm"; f.timeZone = .current; return f
    }()
    private let isoDay: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.timeZone = .current; return f
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
                let items = try await client.availability(acct: account, who: list, start: isoLocal.string(from: start), end: isoLocal.string(from: end))
                guard availabilityToken == token else { return }
                availabilityRows = items.map { ($0.name ?? $0.address ?? "?", TrayAvailability.status(freebusy: $0.freebusy)) }
                availabilityNote = nil
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
```

- [ ] **Step 2: Verify (deferred to Task 14's full build)**

Needs `TrayStatusController` (Task 10) to present it — visual/functional verification happens in Task 14's manual checklist (create a real test event, confirm it shows up in the web calendar; type a known attendee and confirm the color dot matches what the web form shows for the same attendee/time).

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/EventCreateView.swift && git commit -m "feat: add native event creation form with live availability check"
```

---

## Task 13: NotificationScheduler.swift — UserNotifications wrapper

**Files:**
- Create: `eas-bridge/app/NotificationScheduler.swift`

**Interfaces:**
- Consumes: `TrayEvent` (Task 3), `TrayNotificationPlan.plan` (Task 7)
- Produces: `NotificationScheduler` with `requestAuthorizationIfNeeded()` and `reschedule(for events: [TrayEvent])`. Used by Task 10.

- [ ] **Step 1: Write the implementation**

Create `eas-bridge/app/NotificationScheduler.swift`:

```swift
import Foundation
import UserNotifications

final class NotificationScheduler {
    private var scheduledIds: Set<String> = []

    func requestAuthorizationIfNeeded() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
    }

    /// Called on every TrayDataStore refresh with the freshly merged event
    /// list — cancels reminders for events that dropped out (deleted /
    /// already started) and schedules the current ones, via
    /// TrayNotificationPlan's pure diff so this stays a thin side-effecting
    /// wrapper around UNUserNotificationCenter.
    func reschedule(for events: [TrayEvent]) {
        let (toSchedule, toCancel) = TrayNotificationPlan.plan(events: events, now: Date(), previouslyScheduledIds: scheduledIds)
        let center = UNUserNotificationCenter.current()
        if !toCancel.isEmpty { center.removePendingNotificationRequests(withIdentifiers: toCancel) }
        for req in toSchedule {
            let content = UNMutableNotificationContent()
            content.title = req.title
            content.body = req.body
            content.sound = .default
            let interval = max(1, req.fireDate.timeIntervalSinceNow)
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: interval, repeats: false)
            center.add(UNNotificationRequest(identifier: req.identifier, content: content, trigger: trigger))
        }
        scheduledIds.subtract(Set(toCancel))
        scheduledIds.formUnion(toSchedule.map { $0.identifier })
    }
}
```

- [ ] **Step 2: Verify (deferred to Task 14's full build — needs a running, permission-granted app to actually fire a notification)**

- [ ] **Step 3: Commit**

```bash
cd eas-bridge && git add app/NotificationScheduler.swift && git commit -m "feat: schedule color-tagged local notifications before each meeting"
```

---

## Task 14: Wire the tray into main.swift, update the build script, end-to-end verification

**Files:**
- Modify: `eas-bridge/app/main.swift:28-61` (AppDelegate)
- Modify: `eas-bridge/app/build_app.sh`

**Interfaces:**
- Consumes: `TrayStatusController` (Task 10)

- [ ] **Step 1: Add the tray property and instantiate it once the server is up**

In `main.swift`, add a property to `AppDelegate` (alongside the existing `var server: Process?`):

```swift
var tray: TrayStatusController?
```

Then change the existing port-polling `Timer` in `applicationDidFinishLaunching` (currently):

```swift
        Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            if portOpen() { t.invalidate(); self.web.load(URLRequest(url: self.url)) }
            else if Date().timeIntervalSince(self.startedAt) > 90 {
                t.invalidate()
                self.showMessage("Сервер не запустился. Лог: ~/.config/eas-bridge/webapp.log")
            }
        }
```

to:

```swift
        Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            if portOpen() {
                t.invalidate()
                self.web.load(URLRequest(url: self.url))
                self.tray = TrayStatusController()
            }
            else if Date().timeIntervalSince(self.startedAt) > 90 {
                t.invalidate()
                self.showMessage("Сервер не запустился. Лог: ~/.config/eas-bridge/webapp.log")
            }
        }
```

- [ ] **Step 2: Update `build_app.sh` to compile every Swift file and link the new frameworks**

Replace the `swiftc` line in `eas-bridge/app/build_app.sh` (currently `swiftc -O main.swift -o "$APP/Contents/MacOS/EASMail" -framework Cocoa -framework WebKit`) with:

```bash
swiftc -O *.swift -o "$APP/Contents/MacOS/EASMail" -framework Cocoa -framework WebKit -framework SwiftUI -framework Combine -framework UserNotifications
```

- [ ] **Step 3: Build**

```bash
cd eas-bridge/app && bash build_app.sh
```

Expected: `built /Users/olesyaba/Applications/Почта EAS.app` with no compiler errors. If there are compiler errors, fix them in the specific file the error points to before moving on — do not paper over a type mismatch by changing an interface without also updating every task above that depends on it.

- [ ] **Step 4: End-to-end manual verification**

1. Quit any running instance, then `open ~/Applications/Почта\ EAS.app`.
2. **Tray icon appears** in the menu bar within a couple of seconds, initially `📅` then updating to a real label (or staying `📅` if nothing is scheduled for the rest of today/tomorrow).
3. **Label reflects a real meeting**: if there's an upcoming meeting today, confirm the label shows its title and a countdown that decreases roughly every 15s; if a meeting is currently running, confirm the `▶` prefix.
4. **Popover**: click the tray icon, confirm today's (or tomorrow's, if today is empty) events list, each with a colored dot — burgundy for Alfa-Bank items, green for Alfa-Seller items, matching the tab colors already in the main window.
5. **Expand + join link**: click a row with a URL location, confirm it expands showing time range and a clickable link; click it, confirm it opens in the default browser.
6. **Native creation**: click «Создать», fill subject/time/an attendee you know is real, confirm the availability dot appears and matches what typing the same attendee/time into the *existing* web event form (`Alt+Создать` in the main window) shows. Submit, confirm a toast/no error, then confirm the event appears in the web calendar (`Синхронизировать` if needed).
7. **Availability fallback for Alfa-Seller**: repeat step 6 with the Alfa-Seller calendar selected and an Alfa-Seller attendee; since Stalwart has no free/busy lookup, confirm the "not available, checked own calendar only" note appears instead of colored dots (matching the web form's own fallback message).
8. **Notification**: create a test event starting 6 minutes out via the native form (so its 5-minute-before reminder is imminent), confirm a system notification appears roughly 5 minutes before the event start, titled with the correct `🔴 Alfa-Bank` / `🟢 Alfa-Seller` marker.
9. **Keychain** (from Task 2, re-verified here now that the whole app is running): confirm `config.json` has no `password` key and `security find-generic-password -a main -s eas-bridge -w` / `-a seller` both return the real passwords.

- [ ] **Step 5: Commit**

```bash
cd eas-bridge && git add app/main.swift app/build_app.sh && git commit -m "feat: wire the tray controller into the app and build multi-file Swift sources"
```
