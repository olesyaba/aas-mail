package ru.olesyaba.aasmail

import org.junit.Assert.assertEquals
import org.junit.Test

class MeetingAlarmsTest {
    @Test fun failedAccountKeepsItsReminders() {
        val old = setOf("main-1|100", "seller-2|200", "main-3|300")
        // Only Alfa synced: main-3 is gone (cancel), Seller failed (keep seller-2 scheduled).
        assertEquals(setOf("main-3|300"), MeetingAlarms.stale(old, setOf("main-1|100"), setOf("main")))
    }

    @Test fun movedMeetingCancelsOldTime() {
        assertEquals(setOf("main-1|100"), MeetingAlarms.stale(setOf("main-1|100"), setOf("main-1|160"), setOf("main", "seller")))
    }
}
