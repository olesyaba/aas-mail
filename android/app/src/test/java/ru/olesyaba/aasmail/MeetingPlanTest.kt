package ru.olesyaba.aasmail

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MeetingPlanTest {
    private val now = 1_000_000_000_000L
    private fun ev(id: String, inMin: Long, cancelled: Boolean = false, allDay: Boolean = false, acct: String = "main") =
        Meeting(acct, if (acct == "main") "Alfa-Bank" else "Seller", id, "Созвон $id", now + inMin * 60_000, cancelled, allDay, null)

    @Test fun schedulesLeadMinutesBefore() {
        val r = MeetingPlan.plan(listOf(ev("1", 30)), now, 5).single()
        assertEquals(now + 25 * 60_000, r.fireAt)
        assertEquals("🔴 Alfa-Bank", r.title)
        assertEquals("Созвон 1 через 5 мин", r.body)
    }

    @Test fun cancelsDroppedAndCancelled() {
        val out = MeetingPlan.plan(listOf(ev("gone", 30, cancelled = true), ev("past", 3), ev("day", 60, allDay = true)), now, 5)
        assertEquals(emptyList<Reminder>(), out)
    }

    @Test fun movedMeetingGetsNewKey() {
        val a = MeetingPlan.plan(listOf(ev("1", 30)), now, 5).single().key
        val b = MeetingPlan.plan(listOf(ev("1", 45)), now, 5).single().key
        assertNotEquals(a, b)
    }

    @Test fun offWhenLeadIsZero() {
        assertEquals(emptyList<Reminder>(), MeetingPlan.plan(listOf(ev("1", 30)), now, 0))
    }

    @Test fun sellerMarker() {
        assertEquals("🟢 Seller", MeetingPlan.plan(listOf(ev("1", 30, acct = "seller")), now, 5).single().title)
    }

    @Test fun joinUrlPrefersMeetingHosts() {
        assertEquals("https://teams.microsoft.com/l/x",
            MeetingPlan.joinUrl(null, "wiki https://wiki.corp/a then <a href=\"https://teams.microsoft.com/l/x\">join</a>"))
        assertEquals("https://zoom.us/j/1", MeetingPlan.joinUrl("https://zoom.us/j/1", null))
        assertEquals("https://wiki.corp/a", MeetingPlan.joinUrl(null, "see https://wiki.corp/a."))
        assertNull(MeetingPlan.joinUrl("Переговорка 5", null))
    }

    @Test fun joinUrlReadsThreePlacesAndMeetingHostWins() {
        // The separate «Ссылка на встречу» field first, then Место, then the description.
        assertEquals("https://alfaseller.ktalk.ru/vladena",
            MeetingPlan.joinUrl("Переговорная 5", "https://wiki.corp/a", "https://alfaseller.ktalk.ru/vladena"))
        // A video-meeting host wins even over a plain link sitting whole in Место (like the web UI).
        assertEquals("https://teams.microsoft.com/l/x",
            MeetingPlan.joinUrl("https://wiki.corp/room", "<a href=\"https://teams.microsoft.com/l/x\">Join</a>"))
    }

    @Test fun parsesTheMeetingLinkField() {
        val items = JSONArray("""[{"item_id":"e3","subject":"Демо","start_iso":"2026-09-27T10:00:00+03:00",
            "location":"Переговорная 5","body":"","online_meeting":"https://alfaseller.ktalk.ru/vladena"}]""")
        assertEquals("https://alfaseller.ktalk.ru/vladena", MeetingPlan.parse("seller", "Seller", items).single().joinUrl)
    }

    @Test fun parsesServerEvents() {
        val items = JSONArray("""[{"item_id":"e1","subject":"FW: Планёрка","start_iso":"2026-09-27T10:00:00+03:00",
            "is_all_day":false,"meeting_status":"","location":"https://zoom.us/j/1","body":""},
            {"item_id":"e2","subject":"x","start_iso":"bad"}]""")
        val m = MeetingPlan.parse("seller", "Seller", items).single()
        assertEquals("Планёрка", m.subject)
        assertEquals(java.time.OffsetDateTime.parse("2026-09-27T10:00:00+03:00").toInstant().toEpochMilli(), m.start)
        assertEquals("https://zoom.us/j/1", m.joinUrl)
        assertEquals("seller-e1", m.id)
    }
}
