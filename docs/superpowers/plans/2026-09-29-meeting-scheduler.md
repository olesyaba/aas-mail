# Meeting Scheduler (best options + by people) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The event editor gets a right-hand scheduler with two tabs, «Лучшие варианты» and «По людям», fed by one batched free/busy call, plus optional attendees and rooms as real Exchange attendee types and a structured agenda.

**Architecture:** Backend adds `people/schedule` (batched `ResolveRecipients` availability, 2-min cache) and an `AttendeeType` patch driven by a per-request `attendee_types` map. Frontend adds pure functions (`fbSlice`, `rankSlots`, `explainSlot`, `agendaText`) tested in node, and a `mountScheduler(box)` UI that reads the editor's fields and writes the chosen time back.

**Tech Stack:** Python 3.12 stdlib (`webapp.py`, unittest), vanilla JS in `web/index.html`, CSS in `web/ui-kit/app.css`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-29-meeting-scheduler-design.md`

## Global Constraints

- Free/busy code per 30 min: `0` free, `1` tentative, `2` busy, `3` out of office, `4` no data; `people/schedule` strings are `days × 48` codes from local midnight of `start`.
- Batch size 20 addresses per `ResolveRecipients`; at most 200 addresses; `days` 1..7 (UI uses 5, Mon–Fri); cache 120 s.
- Attendee types on the wire: required `1`, optional `2`, resource `3`; UI sends `attendees` (strings, unchanged) + `attendee_types: {address: "optional"|"resource"}`.
- Score: busy required 10, tentative required 3, busy optional 2, each broken constraint 6; up to 6 options, not overlapping within a day.
- Constraints (keys in `prefs.sched_cons`): `not_before_10`, `lunch` (13–14), `fri_late` (Friday after 16:00), `room`.
- `prefs.rooms`: up to 30 `{name, address}`.
- Colour is never the only signal: busy = fill, tentative / out of office = hatching, no data = grey + legend.
- Seller (Stalwart) has no free/busy: the pane says so and checks only the user's own calendar.
- All UI copy in Russian; no new dependencies.

## Review Focus

1. Slots already in the past (today before now) must never be offered as «лучшие» → `past` callback, test in Task 3.
2. A participant typed as a name that the directory cannot resolve must not count as busy, and must be named → `unresolved` in Task 2 test, `noData` in Task 3 test, «Не нашла в каталоге» line in Task 5.
3. 45+ participants must not break the free/busy request → batching test in Task 2.
4. Seller account (availability error) must still let you create the meeting and see your own conflicts → error branch in Task 5, checked on the stand in Task 7.
5. Editing a meeting must keep optional attendees and rooms in their fields and not turn them into required ones → `attOf(type)` in Task 4, `call()` scoping test in Task 1 (update path uses the same map).
6. A meeting longer than the working day → `rankSlots` returns `[]`, pane says «подходящего времени нет» (test in Task 3).

---

### Task 1: Attendee types on the wire

**Files:**
- Modify: `webapp.py` (new `_ATT`, `_ATT_TYPE_CODE`, `_patch_attendee_types()` next to `_patch_event_update_attendees`; `call()` ~line 632; `main()` patch list; `_cal_after_write` create/update attendees)
- Test: `tests/python/test_logic.py` (new `AttendeeTypesTest`), `tests/python/test_http.py` (`UiContractTest` allowed keys)

**Interfaces:**
- Produces: request param `attendee_types: {address: "optional"|"resource"}` accepted by `events/create` and `events/update`; `webapp._ATT.types` (thread-local dict, lower-cased addresses); `webapp._patch_attendee_types()`.

- [ ] **Step 1: Write the failing tests** (append to `tests/python/test_logic.py`)

```python
class AttendeeTypesTest(unittest.TestCase):
    def test_roles_become_attendee_types(self):
        from outlook_activesync_mcp.wbxml import find, find_all, text_of
        webapp._patch_attendee_types()
        webapp._ATT.types = {"opt@x.ru": "optional", "room@x.ru": "resource"}
        try:
            node = cal_cmd._attendees_block(["a@x.ru", "Opt@x.ru", {"address": "room@x.ru", "name": "Байкал"}])
        finally:
            webapp._ATT.types = {}
        codes = [text_of(find(x, "Calendar", "AttendeeType")) for x in find_all(node, "Calendar", "Attendee")]
        self.assertEqual(codes, ["1", "2", "3"])

    def test_call_scopes_roles_to_one_request(self):
        seen = {}
        with mock.patch.object(webapp, "_call_locked", lambda *args: seen.update(webapp._ATT.types) or {"ok": True}):
            webapp.call(make_acct(), "events", {"action": "create", "attendees": ["o@x.ru"],
                                                "attendee_types": {"O@x.ru": "optional", "b@x.ru": "boss"}})
        self.assertEqual(seen, {"o@x.ru": "optional"})
        self.assertEqual(getattr(webapp._ATT, "types", {}), {})
```

- [ ] **Step 2: Run to see them fail**

Run: `bash tests/run_tests.sh py 2>&1 | grep -E "AttendeeTypes|FAILED|OK"`
Expected: FAIL — `AttributeError: module 'webapp' has no attribute '_patch_attendee_types'`

- [ ] **Step 3: Implement** — in `webapp.py`, right after `_patch_event_update_attendees` add:

```python
# Roles for the attendees of the one events create/update running on this thread:
# {address (lower case): "optional" | "resource"}. Set by call() under the account
# lock, read by the patched _attendees_block (thread-local: two accounts may write at once).
_ATT = threading.local()
_ATT_TYPE_CODE = {"optional": "2", "resource": "3"}


def _patch_attendee_types():
    """Upstream writes every attendee as Required (AttendeeType 1): honour the
    roles the editor sends in ``attendee_types`` (optional → 2, room → 3), so
    Outlook shows «необязательный» and the room books itself."""
    try:
        from outlook_activesync_mcp.commands import calendar
        from outlook_activesync_mcp.wbxml import el
    except ImportError:
        return
    if getattr(calendar._attendees_block, "_eas_types_patched", False):
        return

    def block(attendees):
        roles = getattr(_ATT, "types", None) or {}
        people = []
        for a in attendees:
            email = a if isinstance(a, str) else (a.get("address") or a.get("email"))
            name = email if isinstance(a, str) else (a.get("name") or email)
            people.append(el("Calendar", "Attendee",
                             el("Calendar", "AttendeeEmail", text=email),
                             el("Calendar", "AttendeeName", text=name),
                             el("Calendar", "AttendeeType",
                                text=_ATT_TYPE_CODE.get(roles.get(str(email).lower(), ""), "1"))))
        return el("Calendar", "Attendees", *people)

    block._eas_types_patched = True  # type: ignore[attr-defined]
    calendar._attendees_block = block
```

In `call()`, after `cache_only = bool(params.pop("cache_only", False))` add:

```python
    roles = params.pop("attendee_types", None) if domain == "events" else None
```

and replace the locked block at the end of `call()`:

```python
    try:
        _ATT.types = ({str(k).lower(): v for k, v in roles.items() if v in _ATT_TYPE_CODE}
                      if isinstance(roles, dict) else {})
        return _call_locked(a, backend, mod, domain, action, params)
    finally:
        _ATT.types = {}
        backend.lock.release()
```

In `main()` add `_patch_attendee_types()` right after `_patch_event_update_attendees()`.

In `_cal_after_write`, keep the role in the optimistic copy — both places that build `[{"address": x} for x in ...]` become:

```python
[{"address": x, "type": (params.get("attendee_types") or {}).get(x, "required")} for x in att]
```

(in the update branch the list variable is `params["attendees"]`).

In `tests/python/test_http.py`, `UiContractTest.test_every_ui_action_exists_and_params_are_accepted`, extend the app-level set:

```python
            unknown = keys - named - {"mime_invite", "cache_only", "note", "to", "cc", "attendee_types"}  # app-level, handled in webapp
```

- [ ] **Step 4: Run tests**

Run: `bash tests/run_tests.sh py 2>&1 | tail -3`
Expected: `OK` / `ALL PASSED` for py.

- [ ] **Step 5: Commit**

```bash
git add webapp.py tests/python/test_logic.py tests/python/test_http.py
git commit -m "Meetings: optional attendees and rooms go to Exchange as AttendeeType 2/3."
```

---

### Task 2: `people/schedule` + scheduler prefs

**Files:**
- Modify: `webapp.py` (`_schedule`, `_SCHED_CACHE`, route in `call()` next to `find_free_slots`; `DEFAULT_PREFS`; `update_prefs`)
- Modify: `web/index.html:308` (JS `prefs` defaults)
- Test: `tests/python/test_logic.py` (`ScheduleTest`, `PrefsTest.test_scheduler_prefs`), `tests/python/test_http.py` (skip `people/schedule` in the upstream-signature check)

**Interfaces:**
- Produces: `POST /api/people {action: "schedule", who: [str], start: "YYYY-MM-DD", days: int}` → `{ok, action: "schedule", count, items: [{address, name, freebusy}], unresolved: [{query, count}], start, days}`; `prefs.rooms: [{name, address}]`; `prefs.sched_cons: {not_before_10, lunch, fri_late, room: bool}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/python/test_logic.py`)

```python
class ScheduleTest(unittest.TestCase):
    def setUp(self):
        webapp._SCHED_CACHE.clear()

    def test_batches_of_20_pads_and_caches(self):
        calls = []

        def handle(client, action, who=None, start=None, end=None, **_):
            calls.append((action, list(who), start, end))
            return {"items": [{"address": w, "name": w.upper(), "freebusy": "02"} for w in who if w != "ghost"],
                    "unresolved": [{"query": "ghost", "count": 0}] if "ghost" in who else []}

        a = make_acct()
        who = [f"u{i}@x.ru" for i in range(45)] + ["ghost"]
        with mock.patch.object(people_cmd, "handle", handle):
            r = webapp._schedule(a, {"who": who, "start": "2026-10-05", "days": 5})
        self.assertEqual([len(c[1]) for c in calls], [20, 20, 6])
        self.assertEqual(calls[0][0], "availability")
        self.assertEqual(calls[0][2:], ("2026-10-05T00:00", "2026-10-10T00:00"))
        self.assertEqual(r["count"], 45)
        self.assertEqual(len(r["items"][0]["freebusy"]), 240)
        self.assertTrue(r["items"][0]["freebusy"].startswith("024"))
        self.assertEqual(r["unresolved"], [{"query": "ghost", "count": 0}])
        with mock.patch.object(people_cmd, "handle", side_effect=AssertionError("must be cached")):
            self.assertIs(webapp._schedule(a, {"who": list(reversed(who)), "start": "2026-10-05"}), r)

    def test_bad_start_and_server_error(self):
        self.assertFalse(webapp._schedule(make_acct(), {"who": ["a@x.ru"], "start": "завтра"})["ok"])
        with mock.patch.object(people_cmd, "handle", side_effect=RuntimeError("no availability")):
            r = webapp._schedule(make_acct(), {"who": ["a@x.ru"], "start": "2026-10-05"})
        self.assertFalse(r["ok"])
        self.assertIn("no availability", r["message"])
```

and inside `PrefsTest`:

```python
    def test_scheduler_prefs(self):
        out = webapp.update_prefs({"rooms": [{"name": "Байкал", "address": "baikal@x.ru"}, {"name": "x", "address": "nope"}, 5],
                                   "sched_cons": {"lunch": False, "evil": 1}})
        self.assertEqual(out["rooms"], [{"name": "Байкал", "address": "baikal@x.ru"}])
        self.assertEqual(out["sched_cons"], {"not_before_10": True, "lunch": False, "fri_late": True, "room": True})
```

- [ ] **Step 2: Run to see them fail**

Run: `bash tests/run_tests.sh py 2>&1 | grep -E "Schedule|scheduler_prefs|Error" | head`
Expected: FAIL — `AttributeError: ... '_SCHED_CACHE'` and `KeyError: 'rooms'`.

- [ ] **Step 3: Implement** — in `webapp.py` after `_free_slots`:

```python
_SCHED_CACHE: dict[tuple, tuple[float, dict]] = {}
_SCHED_BATCH = 20


def _schedule(a: Acct, params: dict) -> dict:
    """Free/busy of everyone in a meeting for `days` days from local midnight of
    `start`: 48 codes a day per person (0 free, 1 tentative, 2 busy, 3 away,
    4 no data). One ResolveRecipients per 20 addresses, so big invitations stay
    under the server's recipient cap; cached 2 min so re-ranking, tab and
    constraint changes never hit Exchange again."""
    from datetime import datetime, timedelta
    fail = lambda err, msg: {"ok": False, "action": "schedule", "count": 0, "items": [], "error": err, "message": msg}
    who = list(dict.fromkeys(str(x).strip() for x in (params.get("who") or []) if str(x).strip()))[:200]
    days = max(1, min(int(params.get("days") or 5), 7))
    try:
        d0 = datetime.strptime(str(params.get("start") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return fail("bad_request", "start: нужна дата YYYY-MM-DD")
    key = (a.id, tuple(sorted(w.lower() for w in who)), d0.date().isoformat(), days)
    hit = _SCHED_CACHE.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    fmt = "%Y-%m-%dT%H:%M"
    start, end = d0.strftime(fmt), (d0 + timedelta(days=days)).strftime(fmt)
    items, unresolved = [], []
    if who:
        backend = a.get()
        with backend.lock:
            from outlook_activesync_mcp.commands import people
            for i in range(0, len(who), _SCHED_BATCH):
                try:
                    backend._pace()
                    r = people.handle(backend.client, "availability", who=who[i:i + _SCHED_BATCH], start=start, end=end)
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] people/schedule failed: %s", a.id, e)
                    return fail(getattr(e, "code", None) or type(e).__name__, str(e))
                items += r.get("items") or []
                unresolved += r.get("unresolved") or []
    n = days * 48
    out = [{"address": x.get("address"), "name": x.get("name"),
            "freebusy": (str(x.get("freebusy") or "") + "4" * n)[:n]} for x in items]
    res = {"ok": True, "action": "schedule", "count": len(out), "items": out, "unresolved": unresolved,
           "start": d0.date().isoformat(), "days": days}
    _SCHED_CACHE[key] = (time.time(), res)
    return res
```

Route it in `call()` next to `find_free_slots`:

```python
    if domain == "people" and action == "schedule":
        return _schedule(a, params)
```

`DEFAULT_PREFS` (after `work_start/work_end`):

```python
    # Meeting scheduler: saved rooms ({name, address}) and the «Лучшие варианты» constraints.
    "rooms": [],
    "sched_cons": {"not_before_10": True, "lunch": True, "fri_late": True, "room": True},
```

`update_prefs`, next to the `links` branch:

```python
            if k == "rooms":
                v = [{"name": str(x.get("name", ""))[:120], "address": str(x.get("address", ""))[:200]}
                     for x in v[:30] if isinstance(x, dict) and "@" in str(x.get("address", ""))]
            if k == "sched_cons":
                v = {c: bool(v.get(c, d)) for c, d in DEFAULT_PREFS["sched_cons"].items()}
```

`web/index.html:308` — add to the JS `prefs` defaults object: `rooms: [], sched_cons: {not_before_10: true, lunch: true, fri_late: true, room: true}`.

`tests/python/test_http.py`, in the loop of `test_every_ui_action_exists_and_params_are_accepted`, next to the `events/list` skip:

```python
            if (domain, action) == ("people", "schedule"):
                continue  # served by webapp._schedule (batched availability)
```

- [ ] **Step 4: Run tests**

Run: `bash tests/run_tests.sh py 2>&1 | tail -3`
Expected: all Python tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp.py web/index.html tests/python/test_logic.py tests/python/test_http.py
git commit -m "people/schedule: a week of free/busy for the whole invitation, 20 addresses per request; rooms and constraints in prefs."
```

---

### Task 3: Ranking and agenda — pure functions

**Files:**
- Modify: `web/index.html` (top-level, right after `dayHead`)
- Test: `tests/js/ui_logic.test.mjs`

**Interfaces:**
- Produces:
  - `fbSlice(fb: string, day: number, ws: number, we: number) → string[]` — codes of the working day's half-hours, missing → `'4'`.
  - `rankSlots({people: [{address, role: 'req'|'opt'|'room'|'cand', fb}], days=5, dur=2, ws=9, we=18, cons={}, k=6, past=(day, s)=>false}) → [{day, s, reqBusy, reqTent, optBusy, oof, noData, rooms, pen, score}]` — `s` and `dur` in half-hours from `ws:00`, `day` from Monday.
  - `explainSlot(o, nameOf = a => a) → string`.
  - `AGENDA_TPL: {retro, demo, daily}` → arrays of `[title, minutes]`; `agendaText(items: [{t, m, who}]) → string`.

- [ ] **Step 1: Write the failing tests** (append to `tests/js/ui_logic.test.mjs`)

```js
test('scheduler: fbSlice, ranking, constraints, no data, past, too long', () => {
  const {fbSlice, rankSlots, explainSlot} = load(['fbSlice', 'rankSlots', 'explainSlot']);
  const day = w => '0'.repeat(18) + w;   // 09:00 is code 18; working day 9..12 = 6 half-hours
  assert.deepEqual(plain(fbSlice('0'.repeat(48) + day('21'), 1, 9, 10)), ['2', '1']);
  assert.deepEqual(plain(fbSlice('', 0, 9, 10)), ['4', '4']);
  const A = {address: 'a@x', role: 'req', fb: day('220000')};
  const B = {address: 'b@x', role: 'req', fb: day('000010')};
  const O = {address: 'o@x', role: 'opt', fb: day('001100')};
  const base = {days: 1, dur: 2, ws: 9, we: 12, k: 6};
  const r = plain(rankSlots({...base, people: [A, B, O]}));
  assert.deepEqual(r.map(o => o.s), [2, 4, 0]);            // scores 2, 3, 10; 3 and 1 overlap picks
  assert.deepEqual(r[0].optBusy, ['o@x']);
  assert.deepEqual(r[1].reqTent, ['b@x']);
  assert.deepEqual(r[2].reqBusy, ['a@x']);
  const c = plain(rankSlots({...base, people: [A, B, O], cons: {not_before_10: true}}));
  assert.deepEqual(c.find(o => o.s === 0).pen, ['раньше 10:00']);
  const N = {address: 'n@x', role: 'req', fb: ''};
  const nd = plain(rankSlots({...base, people: [A, B, O, N]}));
  assert.deepEqual(nd.map(o => o.s), [2, 4, 0]);            // no data is not busy
  assert.deepEqual(nd[0].noData, ['n@x']);
  const R = {address: 'r@x', role: 'room', fb: day('002222')};
  const rm = plain(rankSlots({...base, people: [A, B, O, R]}));
  assert.ok(rm.find(o => o.s === 2).pen.includes('переговорка занята'));
  const past = plain(rankSlots({...base, people: [A, B, O], past: (d, s) => s < 3}));
  assert.deepEqual(past.map(o => o.s), [4]);
  assert.deepEqual(plain(rankSlots({...base, dur: 7, people: [A]})), []);
  assert.equal(explainSlot({reqBusy: ['a', 'b', 'c', 'd'], reqTent: [], optBusy: ['o'], oof: [], noData: [], pen: ['раньше 10:00']}, x => x.toUpperCase()),
    'Заняты обязательные: A, B, C и ещё 1; опциональные заняты: O; раньше 10:00');
});

test('agenda: numbered text block, empty rows skipped', () => {
  const {agendaText, AGENDA_TPL} = load(['agendaText', 'AGENDA_TPL']);
  assert.equal(agendaText([{t: 'Что получилось', m: 15, who: 'Соколова М.'}, {t: ' ', m: 5}, {t: 'Итоги', m: 0}]),
    'Повестка:\n1. Что получилось — 15 мин (Соколова М.)\n2. Итоги');
  assert.equal(agendaText([]), '');
  assert.equal(AGENDA_TPL.retro.length, 4);
});
```

- [ ] **Step 2: Run to see them fail**

Run: `node --test tests/js/ui_logic.test.mjs 2>&1 | grep -E "^ℹ (pass|fail)|not defined" | head`
Expected: FAIL — `ReferenceError: fbSlice is not defined`

- [ ] **Step 3: Implement** — in `web/index.html` right after `function dayHead(...) {...}`:

```js
/* ---------- meeting scheduler: pure parts ---------- */
/** Working-day half-hours of one day from a people/schedule string (48 codes a day
 *  from the first day's midnight: 0 free, 1 tentative, 2 busy, 3 away, 4 no data). */
const fbSlice = (fb, day, ws, we) => Array.from({length: (we - ws) * 2}, (_, i) => String(fb || '')[day * 48 + ws * 2 + i] || '4');
/** Meeting starts for «Лучшие варианты». people: [{address, role: 'req'|'opt'|'room'|'cand', fb}];
 *  `cand` = a saved room that is not invited: it only says which room is free. `s`/`dur` are
 *  half-hours from ws:00, `day` counts from Monday. Score: busy required 10, tentative
 *  required 3, busy optional 2, each broken constraint 6. Up to k starts, not overlapping
 *  within a day. */
function rankSlots({people, days = 5, dur = 2, ws = 9, we = 18, cons = {}, k = 6, past = () => false}) {
  const n = (we - ws) * 2, out = [];
  const invited = people.filter(p => p.role === 'room'), cand = people.filter(p => p.role === 'cand');
  for (let day = 0; day < days; day++) {
    const codes = people.map(p => fbSlice(p.fb, day, ws, we));
    for (let s = 0; s + dur <= n; s++) {
      if (past(day, s)) continue;
      const o = {day, s, reqBusy: [], reqTent: [], optBusy: [], oof: [], noData: [], rooms: [], pen: []};
      people.forEach((p, i) => {
        const w = codes[i].slice(s, s + dur);
        const c = ['3', '2', '1', '4'].find(x => w.includes(x)) || '0';
        if (p.role === 'room' || p.role === 'cand') { if (c === '0') o.rooms.push(p.address); return; }
        if (c === '2' || c === '3') (p.role === 'req' ? o.reqBusy : o.optBusy).push(p.address);
        if (c === '3') o.oof.push(p.address);
        if (c === '1' && p.role === 'req') o.reqTent.push(p.address);
        if (c === '4' && p.role === 'req') o.noData.push(p.address);
      });
      const h = ws + s / 2, end = h + dur / 2;
      if (cons.not_before_10 && h < 10) o.pen.push('раньше 10:00');
      if (cons.lunch && h < 14 && end > 13) o.pen.push('задевает обед');
      if (cons.fri_late && day === 4 && end > 16) o.pen.push('пятница после 16:00');
      const need = invited.length ? invited : cons.room ? cand : [];
      if (need.length && !need.some(r => o.rooms.includes(r.address))) o.pen.push(invited.length ? 'переговорка занята' : 'нет свободной переговорки');
      o.score = o.reqBusy.length * 10 + o.reqTent.length * 3 + o.optBusy.length * 2 + o.pen.length * 6;
      out.push(o);
    }
  }
  out.sort((a, b) => a.score - b.score || a.day - b.day || a.s - b.s);
  const picked = [];
  for (const o of out) {
    if (picked.every(p => p.day !== o.day || Math.abs(p.s - o.s) >= dur)) picked.push(o);
    if (picked.length === k) break;
  }
  return picked;
}
/** «Заняты обязательные: …; под вопросом: …; …» for one ranked start. */
function explainSlot(o, nameOf = a => a) {
  const list = (arr, max = 3) => { const n = arr.map(nameOf); return n.length > max ? `${n.slice(0, max).join(', ')} и ещё ${n.length - max}` : n.join(', '); };
  const parts = [o.reqBusy.length ? `Заняты обязательные: ${list(o.reqBusy)}` : 'Все обязательные свободны'];
  if (o.reqTent.length) parts.push(`под вопросом: ${list(o.reqTent, 2)}`);
  if (o.optBusy.length) parts.push(`опциональные заняты: ${list(o.optBusy, 2)}`);
  if (o.oof.length) parts.push(`вне офиса: ${list(o.oof, 2)}`);
  if (o.noData.length) parts.push(`нет данных: ${list(o.noData, 2)}`);
  if (o.pen.length) parts.push(o.pen.join(', '));
  return parts.join('; ');
}
const AGENDA_TPL = {
  retro: [['Что получилось', 15], ['Что не получилось', 15], ['Что меняем к следующему релизу', 20], ['Ответственные и сроки', 10]],
  demo: [['Показ', 20], ['Вопросы', 10], ['Решение о запуске', 10]],
  daily: [['Вчера, сегодня, блокеры', 10], ['Разбор блокеров', 5]],
};
/** Agenda rows → the text block that opens the invitation's description. */
function agendaText(items) {
  const rows = (items || []).filter(a => String(a.t || '').trim())
    .map((a, i) => `${i + 1}. ${String(a.t).trim()}${+a.m ? ` — ${+a.m} мин` : ''}${a.who ? ` (${a.who})` : ''}`);
  return rows.length ? 'Повестка:\n' + rows.join('\n') : '';
}
```

- [ ] **Step 4: Run tests**

Run: `node --test tests/js/ui_logic.test.mjs 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: `ℹ fail 0`

- [ ] **Step 5: Commit**

```bash
git add web/index.html tests/js/ui_logic.test.mjs
git commit -m "Scheduler: ranking of meeting starts with who-is-busy explanations, agenda text."
```

---

### Task 4: Editor fields — required / optional / room, save with roles

**Files:**
- Modify: `web/index.html` — `eventForm()` template (participants block, remove `#avw`), save handler, remove `runAvailability` and its wiring (keep the «start past end carries the end» listener); `bindPeopleInput.pick` dispatches `change`; `closeModal` drops `aas-modal--wide`.
- Modify: `web/ui-kit/app.css` — `.aas-evwrap`, wide modal.

**Interfaces:**
- Consumes: `attendee_types` (Task 1), `prefs.rooms` (Task 2).
- Produces: fields `#eatt` (required), `#eopt` (optional), `#eroom` (rooms), container `#sched` (Task 5 mounts there), `rememberRooms(text)`.

- [ ] **Step 1: Template** — in `eventForm` replace `attStr` with:

```js
  const attOf = type => e ? (e.attendees || []).filter(a => a.address && a.address !== e.organizer?.address && (a.type || 'required') === type)
    .map(a => formatPerson(a)).join(', ') : '';
```

wrap the form: `openModal(\`<h3>…</h3><div class="aas-evwrap"><div class="aas-evform"> …all current fields and .acts2… </div><div class="aas-sched" id="sched"></div></div>\`)`, then `$('#modal').classList.add('aas-modal--wide');`.

Replace the «Участники» field and the whole `#avw` block with:

```js
    <div class="f"><label for="eatt">Обязательные участники</label><div class="aas-people"><input type="text" id="eatt" placeholder="фамилия или email" value="${esc(attOf('required'))}"></div></div>
    <div class="f"><label for="eopt">Опциональные</label><div class="aas-people"><input type="text" id="eopt" placeholder="могут не приходить" value="${esc(attOf('optional'))}"></div></div>
    <div class="f"><label for="eroom">Переговорка</label><div class="aas-people"><input type="text" id="eroom" placeholder="найдите по названию, например «Байкал»" value="${esc(attOf('resource'))}"></div>
      ${(prefs.rooms || []).length ? `<div class="aas-slots">${prefs.rooms.map((r, i) => `<button type="button" class="aas-btn aas-btn--sm" data-room="${i}">${esc(r.name || r.address)}</button>`).join('')}</div>` : ''}
      <div class="hint">Подсказки из недавних и каталога — Enter/Tab подставляют.</div></div>
```

- [ ] **Step 2: Wiring** — after `openModal`, replace `bindPeopleInput($('#eatt'));` with:

```js
  ['#eatt', '#eopt', '#eroom'].forEach(id => bindPeopleInput($(id)));
  $('#modal').querySelectorAll('[data-room]').forEach(b => b.onclick = () => {
    const r = prefs.rooms[+b.dataset.room], f = $('#eroom');
    if (!splitAddrs(f.value).map(addrOnly).includes(r.address)) f.value = (f.value.trim() ? f.value.replace(/[,\s]*$/, ', ') : '') + formatPerson(r) + ', ';
    f.dispatchEvent(new Event('change', {bubbles: true}));
  });
```

Delete `const av = $('#eavail'); … runAvailability … if (av) {…}` but keep, as a standalone listener:

```js
  $('#est')?.addEventListener('change', () => {
    const st = new Date($('#est').value), en = new Date($('#een').value);
    if (!isNaN(st) && (isNaN(en) || en <= st)) $('#een').value = isoLocal(new Date(st.getTime() + 30 * 6e4));
  });
```

In `bindPeopleInput`'s `pick`, after `inp.setSelectionRange(pos, pos);` add `inp.dispatchEvent(new Event('change', {bubbles: true}));` (the scheduler reloads on it).

- [ ] **Step 3: Save** — in `$('#esave').onclick`, compute once before `if (editing)`:

```js
    const addrs = id => splitAddrs($(id).value).map(addrOnly).filter(Boolean);
    const req = addrs('#eatt'), opt = addrs('#eopt'), rooms = addrs('#eroom');
    const att = [...new Set([...req, ...opt, ...rooms])];
    const roles = Object.fromEntries([...opt.map(x => [x, 'optional']), ...rooms.map(x => [x, 'resource'])]);
    if (Object.keys(roles).length) p.attendee_types = roles;
    if (rooms.length && !p.location.trim()) p.location = splitAddrs($('#eroom').value).map(s => s.replace(/\s*<[^>]*>\s*$/, '').replace(/^"|"$/g, '').trim()).join(', ');
```

Editing branch: `p.attendees = att;` (empty list keeps the old «clear roster» behaviour). Create branch: `if (att.length) p.attendees = att;`. Replace both `rememberAddrsFromFields($('#eatt').value)` with `rememberAddrsFromFields($('#eatt').value, $('#eopt').value, $('#eroom').value); rememberRooms($('#eroom').value);`.

Add near `rememberAddrsFromFields`:

```js
/** Rooms typed into the editor are remembered for the quick-pick buttons and for
 *  «Лучшие варианты» (which saved room is free). */
function rememberRooms(text) {
  const got = splitAddrs(text).map(s => ({name: s.replace(/\s*<[^>]*>\s*$/, '').replace(/^"|"$/g, '').trim(), address: addrOnly(s)}))
    .filter(r => r.address.includes('@'));
  if (!got.length) return;
  const seen = new Set(got.map(r => r.address.toLowerCase()));
  savePrefs({rooms: [...got, ...(prefs.rooms || []).filter(r => !seen.has(r.address.toLowerCase()))].slice(0, 30)});
}
```

`closeModal()`: add `'aas-modal--wide'` to the `classList.remove(...)` list.

- [ ] **Step 4: CSS** (`web/ui-kit/app.css`, after the `#modal` block)

```css
/* Event editor with the scheduler: form left, «Лучшие варианты / По людям» right */
#modal.aas-modal--wide { width: min(1180px, 100%); }
.aas-evwrap { display: grid; grid-template-columns: minmax(0, 420px) minmax(0, 1fr); gap: 20px; }
.aas-evwrap > * { min-width: 0; }
.aas-sched { border-left: 1px solid var(--aas-line); padding-left: 20px; }
@media (max-width: 900px) {
  .aas-evwrap { grid-template-columns: 1fr; }
  .aas-sched { border-left: 0; padding-left: 0; border-top: 1px solid var(--aas-line); padding-top: 14px; }
}
```

- [ ] **Step 5: Run tests** — `bash tests/run_tests.sh js py 2>&1 | tail -3` → `ALL PASSED` (the DOM-id contract test sees `eopt`, `eroom`, `sched`).

- [ ] **Step 6: Commit**

```bash
git add web/index.html web/ui-kit/app.css
git commit -m "Event editor: required, optional and room fields, roles saved to Exchange, rooms remembered."
```

---

### Task 5: Scheduler pane + «Лучшие варианты»

**Files:**
- Modify: `web/index.html` — new `mountScheduler(box)` after the pure functions; call from `eventForm`.
- Modify: `web/ui-kit/app.css` — `.aas-sched__*`, `.aas-bo*`.

**Interfaces:**
- Consumes: `rankSlots`, `explainSlot`, `fbSlice` (Task 3); `people/schedule` (Task 2); `#eatt/#eopt/#eroom/#est/#een/#eall/#sched` (Task 4).
- Produces: `mountScheduler(box) → {load(), retime()}`. This task ships only the «Лучшие варианты» tab (the «По людям» tab button appears once Task 6 defines `gridHtml`/`bindGrid`).

- [ ] **Step 1: Implement `mountScheduler`**

```js
/* ---------- meeting scheduler (event editor, right side) ----------
   Reads the editor's fields, fetches a Mon–Fri week of free/busy for everyone
   (people/schedule) and writes the chosen start back into «Начало/Конец». */
function mountScheduler(box) {
  const S = {tab: 'best', wk: null, data: null, err: '', tok: 0, key: '', gday: 0, fold: {}};
  const ws = () => prefs.work_start ?? 9, we = () => prefs.work_end ?? 18;
  const cons = () => ({not_before_10: true, lunch: true, fri_late: true, room: true, ...(prefs.sched_cons || {})});
  const monday = d => { const x = startOfDay(d), wd = (x.getDay() + 6) % 7; x.setDate(x.getDate() + (wd > 4 ? 7 - wd : -wd)); return x; };
  const slotDate = (day, s) => new Date(S.wk.getFullYear(), S.wk.getMonth(), S.wk.getDate() + day, ws(), s * 30);
  const hm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const roleList = () => {
    const pick = (id, role) => splitAddrs($(id)?.value).map(addrOnly).filter(Boolean).map(address => ({address, role}));
    const seen = new Set();
    return [...pick('#eatt', 'req'), ...pick('#eopt', 'opt'), ...pick('#eroom', 'room'),
      ...(prefs.rooms || []).map(r => ({address: r.address, role: 'cand'}))]
      .filter(p => { const k = p.address.toLowerCase(); return !seen.has(k) && seen.add(k); });
  };
  const people = () => {
    const by = new Map((S.data?.items || []).map(i => [String(i.address || '').toLowerCase(), i]));
    return roleList().map(p => { const i = by.get(p.address.toLowerCase()); return {...p, name: i?.name || p.address, fb: i?.freebusy || ''}; });
  };
  const cur = () => {
    const st = new Date($('#est').value), en = new Date($('#een').value);
    return isNaN(st) ? null : {st, dur: Math.max(1, Math.round(((isNaN(en) ? st : en) - st) / 18e5))};
  };
  const slotOf = d => {
    const day = Math.round((startOfDay(d) - S.wk) / 864e5), s = (d.getHours() - ws()) * 2 + Math.floor(d.getMinutes() / 30);
    return day >= 0 && day < 5 && s >= 0 && s < (we() - ws()) * 2 ? {day, s} : null;
  };
  const setSlot = (day, s) => {
    const c = cur(), st = slotDate(day, s);
    $('#est').value = isoLocal(st); $('#een').value = isoLocal(new Date(+st + (c ? c.dur : 2) * 18e5));
    $('#modal').dataset.dirty = '1'; S.gday = day; paint();
  };
  const empty = t => `<div class="aas-sched__empty">${esc(t)}</div>`;

  async function load() {
    const c = cur(); if (!S.wk) S.wk = monday(c ? c.st : new Date());
    const ppl = roleList();
    if (!ppl.some(p => p.role !== 'cand')) { S.data = null; S.err = ''; S.key = ''; return paint(); }
    const key = ppl.map(p => p.address.toLowerCase()).join(',') + '|' + isoDay(S.wk);
    if (key === S.key && (S.data || S.err)) return paint();
    S.key = key; S.data = null; S.err = ''; const tok = ++S.tok; paint();
    try {
      const j = await api('people', {action: 'schedule', who: ppl.map(p => p.address), start: isoDay(S.wk), days: 5});
      if (tok === S.tok) S.data = j;
    } catch (x) { if (tok === S.tok) S.err = x.message || 'ошибка сервера'; }
    if (tok === S.tok) paint();
  }
  function retime() {
    const c = cur();
    if (c && S.wk && +monday(c.st) !== +S.wk) { S.wk = monday(c.st); return load(); }
    const p = c && S.wk && slotOf(c.st); if (p) S.gday = p.day;
    paint();
  }
  // Seller has no free/busy for others: at least the user's own conflicts.
  async function ownCheck() {
    const c = cur(), slot = box.querySelector('.aas-sched__own'); if (!c || !slot) return;
    const en = new Date(+c.st + c.dur * 18e5);
    try {
      const l = await api('events', {action: 'list', start: isoDay(c.st), end: isoDay(new Date(+en + 864e5)), limit: 300});
      const hit = (l.items || []).filter(e => { const es = new Date(e.start_iso), ee = e.end ? localDate(e.end) : es; return es < en && ee > c.st && e.busy_status !== 'free'; });
      slot.innerHTML = hit.length ? hit.map(e => `<div class="aas-avline">${avDot('bad')}У вас пересечение: ${esc(evTitle(e))} (${esc(fmtRange(new Date(e.start_iso)))})</div>`).join('')
        : `<div class="aas-avline">${avDot('ok')}У вас в это время свободно</div>`;
    } catch (_) { /* the message above already says what is missing */ }
  }
  function bestHtml() {
    const c = cons(), ppl = people(), cu = cur(), now = new Date();
    const nameOf = a => ppl.find(p => p.address.toLowerCase() === a.toLowerCase())?.name || a;
    const req = ppl.filter(p => p.role === 'req').length;
    const list = cu ? rankSlots({people: ppl, days: 5, dur: cu.dur, ws: ws(), we: we(), cons: c, k: 6, past: (d, s) => slotDate(d, s) < now}) : [];
    const at = cu && slotOf(cu.st);
    const ck = (k, t) => `<label class="chk"><input type="checkbox" data-con="${k}" ${c[k] ? 'checked' : ''}> ${t}</label>`;
    const unres = (S.data.unresolved || []).map(u => u.query).filter(Boolean);
    return `<div class="aas-sched__cons">${ck('not_before_10', 'Не раньше 10:00')}${ck('lunch', 'Не трогать обед 13–14')}${ck('fri_late', 'Не в пятницу после 16:00')}${ppl.some(p => p.role === 'room' || p.role === 'cand') ? ck('room', 'Нужна свободная переговорка') : ''}</div>
      ${unres.length ? `<div class="aas-avline hint">${avDot('')}Не нашла в каталоге: ${esc(unres.join(', '))}. Их занятость не учтена.</div>` : ''}
      ${list.length ? list.map((o, i) => {
        const st = slotDate(o.day, o.s), en = new Date(+st + cu.dur * 18e5), room = o.rooms[0];
        const on = at && at.day === o.day && at.s === o.s;
        return `<button type="button" class="aas-bo${on ? ' on' : ''}" data-pick="${o.day},${o.s}" data-room="${esc(room || '')}">
          <span class="aas-bo__rk">${i + 1}</span>
          <span class="aas-bo__when"><b>${RU_WD_SHORT[st.getDay()]}, ${st.getDate()} ${RU_MON[st.getMonth()]}</b><span>${hm(st)}–${hm(en)}</span>${room ? `<span>${esc(nameOf(room))} свободна</span>` : ''}</span>
          <span class="aas-bo__why">${esc(explainSlot(o, nameOf))}</span>
          <span class="aas-bo__fit" title="Свободны обязательные">${req - o.reqBusy.length} / ${req}</span></button>`;
      }).join('') : empty('На этой неделе подходящего времени нет — попробуйте следующую неделю или снимите ограничения.')}`;
  }
  function paint() {
    const c = cur(); if (!S.wk) S.wk = monday(c ? c.st : new Date());
    const fri = new Date(S.wk.getFullYear(), S.wk.getMonth(), S.wk.getDate() + 4);
    const tab = (id, t) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${S.tab === id}">${t}</button>`;
    const head = `<div class="aas-sched__tabs" role="tablist">${tab('best', 'Лучшие варианты')}${typeof gridHtml === 'function' ? tab('grid', 'По людям') : ''}
      <span class="aas-spacer"></span><button type="button" class="aas-btn aas-btn--sm" data-wk="-1" aria-label="Предыдущая неделя">‹</button>
      <span class="aas-sched__wk">${S.wk.getDate()}–${fri.getDate()} ${RU_MON[fri.getMonth()]}</span>
      <button type="button" class="aas-btn aas-btn--sm" data-wk="1" aria-label="Следующая неделя">›</button></div>`;
    let body;
    if ($('#eall')?.checked) body = empty('Для события на весь день время подбирать не нужно.');
    else if (!roleList().some(p => p.role !== 'cand')) body = empty('Добавьте участников — покажу свободное время.');
    else if (S.err) body = `<div class="aas-sched__empty">Занятость участников недоступна: ${esc(S.err)}. <button type="button" class="aas-linkbtn" data-retry>Повторить</button></div><div class="aas-sched__own"></div>`;
    else if (!S.data) body = `<div class="aas-sched__skel" aria-busy="true" aria-label="Загружаю занятость">${'<i></i>'.repeat(5)}</div>`;
    else body = S.tab === 'grid' && typeof gridHtml === 'function' ? gridHtml() : bestHtml();
    box.innerHTML = head + body;
    if (S.err) ownCheck();
    if (S.tab === 'grid' && typeof bindGrid === 'function') bindGrid();
  }
  box.onclick = e => {
    const b = e.target.closest('button'); if (!b) return; const d = b.dataset;
    if (d.tab) { S.tab = d.tab; return paint(); }
    if (d.wk) { S.wk = new Date(S.wk.getFullYear(), S.wk.getMonth(), S.wk.getDate() + 7 * +d.wk); return load(); }
    if (d.retry !== undefined) { S.key = ''; return load(); }
    if (d.pick) {
      const [day, s] = d.pick.split(',').map(Number), f = $('#eroom');
      const saved = (prefs.rooms || []).find(r => r.address === d.room);
      if (saved && !f.value.trim()) f.value = formatPerson(saved) + ', ';   // «подобрать»: the free saved room
      return setSlot(day, s);
    }
    if (d.gday) { S.gday = +d.gday; return paint(); }
    if (d.fold) { S.fold[d.fold] = !S.fold[d.fold]; return paint(); }
  };
  box.onchange = e => { const k = e.target.dataset.con; if (k) { savePrefs({sched_cons: {...cons(), [k]: e.target.checked}}); paint(); } };
  return {load, retime};
}
```

Note: `gridHtml`/`bindGrid` are declared inside `mountScheduler` in Task 6 (they need `S`), so the `typeof` guards in this task go away in Task 6 — the tab button appears once they exist.

- [ ] **Step 2: Mount from `eventForm`** (after the Task 4 wiring):

```js
  const sched = mountScheduler($('#sched'));
  let schedT;
  const reload = () => { clearTimeout(schedT); schedT = setTimeout(sched.load, 450); };
  ['#eatt', '#eopt', '#eroom'].forEach(id => ['input', 'change'].forEach(ev => $(id).addEventListener(ev, reload)));
  ['#est', '#een', '#eall'].forEach(id => $(id)?.addEventListener('change', sched.retime));
  sched.load();
```

- [ ] **Step 3: CSS**

```css
.aas-sched__tabs { display: flex; align-items: center; gap: 6px; margin-bottom: 12px; }
.aas-sched__tabs [role="tab"] { border: 0; background: none; padding: 6px 10px; border-radius: var(--aas-r-md); color: var(--aas-muted); font: inherit; font-weight: 600; cursor: pointer; }
.aas-sched__tabs [role="tab"][aria-selected="true"] { background: var(--aas-accent-bg); color: var(--aas-accent-text); }
.aas-sched__tabs [role="tab"]:focus-visible { outline: 2px solid var(--aas-accent-text); outline-offset: 2px; }
.aas-sched__wk { min-width: 96px; text-align: center; font-size: var(--aas-t-sm); color: var(--aas-muted); font-variant-numeric: tabular-nums; }
.aas-sched__cons { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-bottom: 10px; font-size: var(--aas-t-sm); }
.aas-sched__cons input { accent-color: var(--aas-accent); }
.aas-sched__empty { padding: 28px 8px; text-align: center; color: var(--aas-muted); }
.aas-sched__own { margin-top: 4px; }
.aas-sched__skel i { display: block; height: 56px; margin-bottom: 8px; border-radius: var(--aas-r-md);
  background: linear-gradient(90deg, var(--aas-panel-2), var(--aas-line), var(--aas-panel-2)); background-size: 200% 100%; animation: aas-skel 1.2s linear infinite; }
@keyframes aas-skel { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .aas-sched__skel i { animation: none; } }
.aas-bo { display: grid; grid-template-columns: 24px 124px minmax(0, 1fr) auto; gap: 10px; align-items: center; width: 100%;
  margin-bottom: 6px; padding: 10px 12px; border: 1px solid var(--aas-line); border-radius: var(--aas-r-md);
  background: var(--aas-panel); color: inherit; font: inherit; text-align: left; cursor: pointer; }
.aas-bo:hover { border-color: var(--aas-line-strong); }
.aas-bo:focus-visible { outline: 2px solid var(--aas-accent-text); outline-offset: 2px; }
.aas-bo.on { border-color: var(--aas-accent-text); box-shadow: inset 0 0 0 1px var(--aas-accent-text); }
.aas-bo__rk { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 700; background: var(--aas-panel-2); color: var(--aas-muted); }
.aas-bo.on .aas-bo__rk { background: var(--aas-accent); color: var(--aas-on-accent); }
.aas-bo__when { display: grid; font-size: var(--aas-t-sm); color: var(--aas-muted); }
.aas-bo__when b { font-size: var(--aas-t-md); color: var(--aas-text); }
.aas-bo__why { font-size: var(--aas-t-sm); line-height: 1.4; color: var(--aas-muted); }
.aas-bo__fit { font-size: var(--aas-t-sm); font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
```

- [ ] **Step 4: Run tests** — `bash tests/run_tests.sh js py 2>&1 | tail -3` → `ALL PASSED`.

- [ ] **Step 5: Stand check** — headless harness (`mock.js` from the 1.2.26 check, extended with a `people/schedule` answer and a hash action that opens «Новое событие»): screenshot shows the tabs, constraints and 6 cards; click on card 2 changes «Начало».

- [ ] **Step 6: Commit**

```bash
git add web/index.html web/ui-kit/app.css
git commit -m "Event editor: «Лучшие варианты» — a ranked week of starts with who is busy and why."
```

---

### Task 6: «По людям» grid

**Files:**
- Modify: `web/index.html` — `gridHtml()` and `bindGrid()` inside `mountScheduler`, drop the `typeof` guards in `paint()`.
- Modify: `web/ui-kit/app.css` — `.aas-pg*`.

**Interfaces:**
- Consumes: `S`, `people()`, `cur()`, `slotOf()`, `slotDate()`, `setSlot()`, `ws()`, `we()` from Task 5; `fbSlice`.

- [ ] **Step 1: Implement** (inside `mountScheduler`, before `paint`):

```js
  function gridHtml() {
    const ppl = people(), n = (we() - ws()) * 2, d = S.gday, cu = cur(), at = cu && slotOf(cu.st);
    const req = ppl.filter(p => p.role === 'req');
    const row = p => fbSlice(p.fb, d, ws(), we()).map(c => `<i class="aas-pg__c aas-pg__c--${c}"></i>`).join('');
    const sum = Array.from({length: n}, (_, s) => {
      const b = req.filter(p => /[23]/.test(fbSlice(p.fb, d, ws(), we())[s])).length;
      return `<i class="aas-pg__c aas-pg__c--sum" style="--r:${req.length ? b / req.length : 0}" title="Заняты ${b} из ${req.length}"></i>`;
    }).join('');
    let names = '<div class="aas-pg__n"></div><div class="aas-pg__n aas-pg__n--head">Все обязательные</div>';
    let rows = `<div class="aas-pg__r aas-pg__r--hdr">${Array.from({length: n}, (_, s) => `<span>${s % 2 ? '' : pad(ws() + s / 2) + ':00'}</span>`).join('')}</div><div class="aas-pg__r">${sum}</div>`;
    [['req', 'Обязательные'], ['opt', 'Опциональные'], ['room', 'Переговорки']].forEach(([role, title]) => {
      const g = ppl.filter(p => p.role === role); if (!g.length) return;
      names += `<button type="button" class="aas-pg__n aas-pg__n--grp" data-fold="${role}" aria-expanded="${!S.fold[role]}">${S.fold[role] ? '▸' : '▾'} ${title} · ${g.length}</button>`;
      rows += '<div class="aas-pg__r aas-pg__r--grp"></div>';
      if (!S.fold[role]) g.forEach(p => {
        names += `<div class="aas-pg__n" title="${esc(p.address)}">${avatar({name: p.name, address: p.address})}<span>${esc(p.name)}</span></div>`;
        rows += `<div class="aas-pg__r">${row(p)}</div>`;
      });
    });
    const days = Array.from({length: 5}, (_, i) => { const x = slotDate(i, 0); return `<button type="button" data-gday="${i}" aria-pressed="${i === d}">${RU_WD_SHORT[x.getDay()]} ${x.getDate()}</button>`; }).join('');
    const en = cu && new Date(+cu.st + cu.dur * 18e5);
    const frame = at && at.day === d ? `<div class="aas-pg__sel" tabindex="0" role="slider" aria-label="Время встречи, стрелки двигают на 30 минут"
      aria-valuenow="${at.s}" aria-valuemin="0" aria-valuemax="${n - cu.dur}" aria-valuetext="${pad(cu.st.getHours())}:${pad(cu.st.getMinutes())}–${pad(en.getHours())}:${pad(en.getMinutes())}"
      style="left:${at.s / n * 100}%;width:${cu.dur / n * 100}%"></div>` : '';
    return `<div class="aas-pg__bar"><span class="aas-seg aas-seg--sm">${days}</span>
        <span class="aas-pg__legend"><span><i class="aas-pg__c--2"></i>занят</span><span><i class="aas-pg__c--1"></i>под вопросом</span><span><i class="aas-pg__c--3"></i>вне офиса</span><span><i class="aas-pg__c--4"></i>нет данных</span></span></div>
      <div class="aas-pg" style="--n:${n}"><div class="aas-pg__names">${names}</div><div class="aas-pg__grid">${rows}<div class="aas-pg__hit"></div>${frame}</div></div>`;
  }
  function bindGrid() {
    const grid = box.querySelector('.aas-pg__grid'), cu = cur(); if (!grid || !cu) return;
    const n = (we() - ws()) * 2, max = Math.max(0, n - cu.dur);
    const sAt = x => { const r = grid.getBoundingClientRect(); return Math.max(0, Math.min(max, Math.floor((x - r.left) / r.width * n))); };
    grid.querySelector('.aas-pg__hit').onclick = e => setSlot(S.gday, sAt(e.clientX));
    const sel = grid.querySelector('.aas-pg__sel'); if (!sel) return;
    const at = slotOf(cu.st);
    sel.onpointerdown = e => {
      sel.setPointerCapture(e.pointerId);
      const off = sAt(e.clientX) - at.s; let last = at.s;
      sel.onpointermove = m => { const s = Math.max(0, Math.min(max, sAt(m.clientX) - off)); if (s !== last) { last = s; sel.style.left = s / n * 100 + '%'; } };
      sel.onpointerup = () => { sel.onpointermove = null; if (last !== at.s) setSlot(S.gday, last); };
    };
    sel.onkeydown = e => {
      const k = {ArrowLeft: -1, ArrowRight: 1}[e.key]; if (!k) return;
      e.preventDefault(); setSlot(S.gday, Math.max(0, Math.min(max, at.s + k))); box.querySelector('.aas-pg__sel')?.focus();
    };
  }
```

In `paint()` replace the guarded bits with plain `tab('grid', 'По людям')`, `S.tab === 'grid' ? gridHtml() : bestHtml()`, `if (S.tab === 'grid') bindGrid();`. In `load()`/`retime()` set `S.gday` from the start when it lands in the week: in `load()` after computing `c` add `const p0 = c && slotOf(c.st); if (p0) S.gday = p0.day;` (after `S.wk` is set).

- [ ] **Step 2: CSS**

```css
.aas-pg__bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin-bottom: 8px; }
.aas-pg__legend { display: flex; flex-wrap: wrap; gap: 10px; font-size: var(--aas-t-xs); color: var(--aas-muted); }
.aas-pg__legend i { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px; vertical-align: -1px; }
.aas-pg { display: grid; grid-template-columns: 190px minmax(0, 1fr); max-height: 62vh; overflow-y: auto; border: 1px solid var(--aas-line); border-radius: var(--aas-r-md); }
.aas-pg__names, .aas-pg__grid { min-width: 0; }
.aas-pg__grid { position: relative; }
.aas-pg__n, .aas-pg__r { box-sizing: border-box; height: 28px; border-bottom: 1px solid color-mix(in srgb, var(--aas-line) 55%, transparent); }
.aas-pg__n { display: flex; align-items: center; gap: 6px; padding: 0 8px; font-size: var(--aas-t-sm); white-space: nowrap; overflow: hidden; }
.aas-pg__n span { overflow: hidden; text-overflow: ellipsis; }
.aas-pg__n .aas-av { flex: none; width: 20px; height: 20px; border-radius: 6px; font-size: 9px; }
.aas-pg__n--head { font-weight: 600; }
.aas-pg__n--grp { width: 100%; border: 0; border-bottom: 1px solid color-mix(in srgb, var(--aas-line) 55%, transparent); background: var(--aas-panel-2);
  color: var(--aas-muted); font: inherit; font-size: var(--aas-t-xs); letter-spacing: .04em; text-transform: uppercase; text-align: left; cursor: pointer; }
.aas-pg__r { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); }
.aas-pg__r--grp { background: var(--aas-panel-2); }
.aas-pg__r--hdr span { padding: 8px 0 0 2px; border-left: 1px solid var(--aas-line); font-size: 10px; color: var(--aas-muted); white-space: nowrap; }
.aas-pg__c { margin: 5px 0; border-left: 1px solid color-mix(in srgb, var(--aas-line) 45%, transparent); }
.aas-pg__c--2 { background: var(--aas-danger); }
.aas-pg__c--1 { background: repeating-linear-gradient(135deg, var(--aas-warning) 0 3px, transparent 3px 6px); }
.aas-pg__c--3 { background: repeating-linear-gradient(45deg, #8b7fd1 0 3px, transparent 3px 6px); }
.aas-pg__c--4 { background: var(--aas-line); }
.aas-pg__c--sum { margin: 3px 0; background: color-mix(in srgb, var(--aas-danger) calc(var(--r) * 85%), color-mix(in srgb, var(--aas-ok) 28%, transparent)); }
.aas-pg__hit { position: absolute; inset: 28px 0 0 0; cursor: crosshair; }
.aas-pg__sel { position: absolute; top: 28px; bottom: 0; border: 2px solid var(--aas-accent); border-radius: 6px;
  background: color-mix(in srgb, var(--aas-accent) 12%, transparent); cursor: grab; touch-action: none; }
.aas-pg__sel:focus-visible { outline: 2px solid var(--aas-accent-text); outline-offset: 2px; }
```

- [ ] **Step 3: Run tests** — `bash tests/run_tests.sh js py 2>&1 | tail -3` → `ALL PASSED`.
- [ ] **Step 4: Stand check** — screenshot of «По людям»: rows by group, summary row, frame over the chosen time; a click on the grid moves «Начало»; ←/→ on the frame moves it by 30 min.
- [ ] **Step 5: Commit**

```bash
git add web/index.html web/ui-kit/app.css
git commit -m "Event editor: «По людям» — half-hour grid per person, draggable meeting frame."
```

---

### Task 7: Agenda block, stand + live check

**Files:**
- Modify: `web/index.html` (`eventForm`: agenda markup above «Описание», handlers, save), `web/ui-kit/app.css` (`.aas-ag`).

**Interfaces:**
- Consumes: `AGENDA_TPL`, `agendaText` (Task 3).

- [ ] **Step 1: Markup** — above the «Описание» field:

```js
    <div class="f"><label>Повестка</label>
      <div class="aas-slots"><button type="button" class="aas-btn aas-btn--sm" data-tpl="retro">Ретро</button><button type="button" class="aas-btn aas-btn--sm" data-tpl="demo">Демо</button><button type="button" class="aas-btn aas-btn--sm" data-tpl="daily">Дейли</button></div>
      <div id="eag"></div><button type="button" class="aas-linkbtn" id="eagadd">+ пункт</button><div class="hint" id="eagsum"></div></div>
```

- [ ] **Step 2: Handlers** (in `eventForm`, after the scheduler mount):

```js
  const AG = [];
  const agWho = () => [...splitAddrs($('#eatt').value), ...splitAddrs($('#eopt').value)]
    .map(s => s.replace(/\s*<[^>]*>\s*$/, '').replace(/^"|"$/g, '').trim()).filter(Boolean);
  const agSum = () => {
    const sum = AG.reduce((t, a) => t + (+a.m || 0), 0), st = new Date($('#est').value), en = new Date($('#een').value);
    const len = Math.max(0, Math.round((en - st) / 6e4) || 0), el = $('#eagsum');
    el.classList.toggle('aas-warn', sum > len);
    el.textContent = !AG.length ? '' : sum > len ? `Пункты занимают ${sum} мин, а встреча ${len}. Сократите повестку или продлите встречу.` : `${sum} из ${len} мин расписано.`;
  };
  const agPaint = () => {
    const who = agWho();
    $('#eag').innerHTML = AG.map((a, i) => `<div class="aas-ag">
      <input type="text" data-ag="${i}" data-k="t" value="${esc(a.t)}" placeholder="Пункт" aria-label="Пункт ${i + 1}">
      <input type="number" data-ag="${i}" data-k="m" value="${+a.m || ''}" min="0" step="5" aria-label="Минут">
      <select data-ag="${i}" data-k="who" aria-label="Кто ведёт"><option value="">Кто ведёт</option>${[...new Set([a.who, ...who].filter(Boolean))].map(p => `<option ${p === a.who ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select>
      <button type="button" class="aas-btn aas-btn--sm" data-agdel="${i}" aria-label="Убрать пункт">×</button></div>`).join('');
    agSum();
  };
  $('#eag').addEventListener('input', e => { const d = e.target.dataset; if (d.ag) { AG[+d.ag][d.k] = e.target.value; agSum(); } });
  $('#eag').addEventListener('change', e => { const d = e.target.dataset; if (d.ag) AG[+d.ag][d.k] = e.target.value; });
  $('#eag').addEventListener('click', e => { const b = e.target.closest('[data-agdel]'); if (b) { AG.splice(+b.dataset.agdel, 1); agPaint(); } });
  $('#eagadd').onclick = () => { AG.push({t: '', m: 10, who: ''}); agPaint(); $('#eag').querySelector(`[data-ag="${AG.length - 1}"]`)?.focus(); };
  $('#modal').querySelectorAll('[data-tpl]').forEach(b => b.onclick = () => { AG.splice(0, AG.length, ...AGENDA_TPL[b.dataset.tpl].map(([t, m]) => ({t, m, who: ''}))); agPaint(); });
  ['#est', '#een'].forEach(id => $(id).addEventListener('change', agSum));
```

Save handler, after `p` is built: `const ag = agendaText(AG); if (ag) p.body = ag + (p.body.trim() ? '\n\n' + p.body : '');`

CSS:

```css
.aas-ag { display: grid; grid-template-columns: minmax(0, 1fr) 64px 130px auto; gap: 6px; align-items: center; margin-bottom: 6px; }
.aas-warn { color: var(--aas-warning-text); }
```

- [ ] **Step 3: Run tests** — `bash tests/run_tests.sh 2>&1 | tail -3` → `ALL PASSED`.
- [ ] **Step 4: Stand check** — headless harness: editor with 3 required + 1 optional + 1 room; both tabs; «Ретро» template; screenshot; console clean. Seller path: mock `people/schedule` → `ok:false` → pane shows «Занятость участников недоступна…» and the own-calendar line.
- [ ] **Step 5: Live check (Alfa-Bank)** — install locally (as for 1.2.26), create a meeting for tomorrow with one optional colleague and a room; in Outlook Web the colleague is «Необязательный», the room accepted/declined by itself; free/busy for 20+ people loads in the pane.
- [ ] **Step 6: Commit**

```bash
git add web/index.html web/ui-kit/app.css
git commit -m "Event editor: agenda with templates, minutes and owners; goes into the invitation."
```
