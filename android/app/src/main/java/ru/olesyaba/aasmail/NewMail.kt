package ru.olesyaba.aasmail

import org.json.JSONArray

data class MailItem(val itemId: String, val isRead: Boolean, val from: String, val subject: String)

/** New-mail detection between two background syncs — the same rule as the web UI's
 *  knownMailIds: the first look at an Inbox only remembers, later looks report unseen unread. */
object NewMail {
    private const val KEEP = 2500

    fun diff(items: List<MailItem>, known: Set<String>?): Pair<List<MailItem>, Set<String>> {
        val seen = LinkedHashSet(known ?: emptySet())
        val fresh = if (known == null) emptyList() else items.filter { !it.isRead && it.itemId !in seen }
        items.forEach { seen.add(it.itemId) }
        val trimmed = if (seen.size > KEEP) seen.toList().takeLast(KEEP).toSet() else seen
        return fresh to trimmed
    }

    fun parse(items: JSONArray): List<MailItem> = (0 until items.length()).map { i ->
        val o = items.getJSONObject(i)
        val f = o.optJSONObject("from")
        MailItem(o.getString("item_id"), o.optBoolean("is_read"),
            f?.optString("name")?.ifBlank { null } ?: f?.optString("address").orEmpty(),
            o.optString("subject").ifBlank { "(без темы)" })
    }
}
