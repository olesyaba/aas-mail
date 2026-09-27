package ru.olesyaba.aasmail

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NewMailTest {
    private fun m(id: String, read: Boolean = false) = MailItem(id, read, "Кто-то", "Тема $id")

    @Test fun firstRunIsSilent() {
        val (fresh, known) = NewMail.diff(listOf(m("a"), m("b")), null)
        assertTrue(fresh.isEmpty())
        assertEquals(setOf("a", "b"), known)
    }

    @Test fun onlyUnseenUnread() {
        val (fresh, known) = NewMail.diff(listOf(m("c"), m("d", read = true), m("a")), setOf("a"))
        assertEquals(listOf("c"), fresh.map { it.itemId })
        assertEquals(setOf("a", "c", "d"), known)
    }

    @Test fun knownSetIsBounded() {
        val big = (1..4001).map { "x$it" }.toSet()
        val (_, known) = NewMail.diff(listOf(m("new")), big)
        assertTrue(known.size <= 2500)
        assertTrue("new" in known)
    }

    @Test fun parsesServerItems() {
        val items = JSONArray("""[{"item_id":"i1","is_read":false,"subject":"","from":{"name":"","address":"a@b.ru"}},
            {"item_id":"i2","is_read":true,"subject":"Привет","from":{"name":"Олеся","address":"o@b.ru"}}]""")
        assertEquals(listOf(MailItem("i1", false, "a@b.ru", "(без темы)"), MailItem("i2", true, "Олеся", "Привет")),
            NewMail.parse(items))
    }
}
