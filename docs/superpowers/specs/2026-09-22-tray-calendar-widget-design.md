# Tray calendar widget — design

Date: 2026-09-22
Status: approved, ready for implementation planning

## Goal

Add a macOS menu-bar (tray) presence to «Почта EAS.app», inspired by
`mac-owa-widget` but scoped to only what was asked for:

1. Tray icon showing the current time / next meeting.
2. Popover to browse today's events, expand one, and follow its join link.
3. Native event creation, with live attendee availability checking.
4. Local notifications before a meeting starts, color-tagged by account.
5. Account passwords moved out of plaintext `config.json` into the macOS
   Keychain.

Explicitly out of scope (not requested, not built): Sparkle auto-update,
other calendar providers (Google/etc.), global hotkeys, engagement stats,
multi-window support — anything from `mac-owa-widget` beyond the five
items above.

## Why native SwiftUI instead of reusing the WKWebView UI

Considered reusing the existing web UI (`eventView`/`eventForm`/
`people availability`) inside a small popover WKWebView — much less code,
but the person explicitly asked for a native popup "как в OWA Widget", so
the tray gets its own SwiftUI views. Event data still comes from the
existing local JSON API (`/api/events`, `/api/people`) — no duplicate
sync/backend logic, only a native presentation layer on top of it.

## Architecture

New files under `eas-bridge/app/`, alongside the existing `main.swift`:

- `TrayModels.swift` — `TrayEvent` (subject, start, end, location, body,
  attendees, account id/name/color, is_all_day, busy_status) decoded from
  the existing `/api/events` JSON shape.
- `TrayAPIClient.swift` — thin HTTP client for `127.0.0.1:8780`: reads the
  token (see below), does the JSON POSTs the web UI already does
  (`events/list`, `events/create`, `people/availability`).
- `TrayDataStore.swift` — `ObservableObject` that polls both accounts
  every 60s, merges + sorts events, exposes `nextEvent`/`todayEvents` to
  the UI, and drives notification scheduling.
- `TrayStatusController.swift` — owns the `NSStatusItem`, the label
  countdown timer (recomputes from cached data every ~15s, no network),
  and the `NSPopover` hosting the SwiftUI content.
- `TrayPopoverView.swift` — event list + expand/detail + join link.
- `EventCreateView.swift` — native creation form + availability check.
- `NotificationScheduler.swift` — wraps `UserNotifications`: requests
  permission once, (re)schedules a reminder per event on every data
  refresh (cancels stale ones first to avoid duplicates on reschedule).

`main.swift`'s `AppDelegate` creates a `TrayStatusController` once the
local server is confirmed up (reusing the existing `portOpen()` wait
logic already there for the main window).

`build_app.sh` changes from `swiftc -O main.swift` to compiling every
`*.swift` file in `app/` (order doesn't matter to `swiftc`), and adds the
`UserNotifications` and `Security` frameworks to the link step, plus the
`NSUserNotificationUsageDescription`-equivalent Info.plist keys required
for local notifications from an unsigned/ad-hoc-signed app.

## Auth: token hand-off to the native layer

The CSRF token is currently only embedded in the HTML the WKWebView
loads (`webapp.py` generates `TOKEN` per run, replaces `__TOKEN__` in
`index.html`). The tray needs it too for its own HTTP calls.

`webapp.py` additionally writes the token to
`~/.config/eas-bridge/runtime_token` (mode 600) right after generating
it, overwritten every run. `TrayAPIClient` reads this file once at
startup. No new HTTP endpoint is added — this keeps the same local-file
trust boundary already used for `config.json` and `webapp.log`, and
doesn't weaken the existing Host/Origin/token guard against browser-based
access.

## Keychain for account passwords

`save_account_config()` in `webapp.py` (already built) currently writes
the password straight into `config.json`. It changes to:

- store the password via `security add-generic-password -a <account_id>
  -s eas-bridge -w <password> -U` (subprocess call, `-U` to update
  in-place), one Keychain item per account (`main` / `seller`);
- write everything except the password to `config.json` as today;
- on startup / reconnect, read the password back with
  `security find-generic-password -a <account_id> -s eas-bridge -w`
  instead of `cfg["password"]`.

This is done from Python via the `security` CLI — no new Swift-side
Keychain code is needed for this part, and the existing web settings form
(built earlier this session) keeps working unchanged; only its backend
storage moves. A `config.json` written before this change (with a
plaintext password) is migrated on first read: if `security
find-generic-password` finds nothing yet, fall back to the plaintext
value already in `config.json`, store it into Keychain, and drop it from
the file on next save.

## Data source & merge

`TrayDataStore` calls `events/list` for `main`, and for `seller` if
configured, each covering "now → end of day" (falling back to "tomorrow"
if today has nothing left), merges by start time, and tags each event
with its source account's name + brand color (the same burgundy `#501820`
/ green `#003830` pair already used in the web UI's tabs and the account
settings blocks — reused as-is for visual consistency instead of
inventing a second palette).

Refresh: every 60s while the popover exists (matches the web UI's own
background cadence); the menu-bar label's countdown re-renders from the
already-fetched data every ~15s without hitting the network.

## Menu bar label

- A meeting is in progress → `▶ <title>`.
- Otherwise, if one is scheduled later today → `<title> · через 12 мин`
  (countdown rounds to minutes, updates in place).
- Nothing left today → a plain calendar glyph, no text.
- Click → toggles the popover.

## Popover

- Header: today's date; falls back to "Завтра, <date>" if today is
  empty and tomorrow's events are shown instead.
- Rows: time, title, a small colored dot for the source account.
- Click a row → expands inline: full time range, location (linkified the
  same way the web UI already does, so a Teams/Zoom/KTalk URL is a
  clickable link), attendee count.
- Footer: "Создать" button opens `EventCreateView` (see below) — a
  sheet/popover-in-popover, not the main window.

## Native event creation (`EventCreateView`)

Fields: account picker (Alfa-Bank / Alfa-Seller), subject, start/end
date-time, location, attendees (comma-separated, matching the web form's
input convention), description. Submits via `TrayAPIClient` to the
existing `events/create` action — no backend changes needed, this action
already exists and is exercised by the web UI today.

## Native availability check

As attendees are typed, debounced calls to the existing `people
availability` action (same one the web UI already calls) return
free/busy per attendee; each gets a colored dot next to their name:
green (free), red (busy/tentative), gray (unknown) — same three-state
mapping the web UI already uses (`index.html:828-842`), reimplemented as
a small SwiftUI view.

## Notifications

- `NotificationScheduler` requests `UNUserNotificationCenter` permission
  once, on first launch of this feature (not re-asked if denied).
- On every `TrayDataStore` refresh: cancel all pending
  `eas-bridge-event-*` notification requests, then schedule one per
  upcoming event at `start - 5 minutes` (only for events still in the
  future).
- **Platform constraint:** macOS does not allow an app to paint a custom
  background color on a notification banner — that chrome is entirely
  system-controlled. The color-by-account request is met instead with a
  text marker in the notification title: `🔴 Alfa-Bank` / `🟢
  Alfa-Seller` prefix, using the same red/green association as the rest
  of the app. Confirmed acceptable.

## Out of scope (explicit)

Sparkle auto-update, Google Calendar/other providers, global hotkeys,
engagement stats, meeting-platform icon detection beyond plain link
detection, multi-window popovers, custom notification sounds/themes.

## Testing / verification plan

- Manual: launch the app, confirm the status item appears, label updates
  around a real upcoming meeting, popover lists today's events from both
  accounts with correct colors, clicking a location link opens the
  browser, creating a test event via the native form shows up in the web
  calendar and vice versa, availability dots match what the web form
  shows for the same attendees, a scheduled notification actually fires
  ~5 minutes before a test event.
- `security find-generic-password` used to confirm the password is no
  longer readable from `config.json` after a save, and that the app still
  authenticates after a restart (proving the Keychain round-trip works).
