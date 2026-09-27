package ru.olesyaba.aasmail

import org.json.JSONArray
import java.time.OffsetDateTime

data class Meeting(val accountId: String, val accountName: String, val itemId: String, val subject: String,
                   val start: Long, val cancelled: Boolean, val allDay: Boolean, val joinUrl: String?) {
    val id get() = "$accountId-$itemId"
}

data class Reminder(val key: String, val fireAt: Long, val title: String, val body: String, val joinUrl: String?)

/** Port of app/TrayNotificationPlan.swift + TrayJoinLink (TrayModels.swift). */
object MeetingPlan {
    fun plan(events: List<Meeting>, now: Long, leadMinutes: Int): List<Reminder> {
        if (leadMinutes <= 0) return emptyList()
        return events.filter { !it.cancelled && !it.allDay }.mapNotNull { e ->
            val fire = e.start - leadMinutes * 60_000L
            if (fire <= now) return@mapNotNull null
            val marker = if (e.accountId == "seller") "🟢" else "🔴"
            Reminder("${e.id}|${e.start}", fire, "$marker ${e.accountName}", "${e.subject} через $leadMinutes мин", e.joinUrl)
        }
    }

    private val forwardPrefix = Regex("""^\s*((re|fwd?|fw|отв|пер)\s*:\s*)+""", RegexOption.IGNORE_CASE)

    fun parse(accountId: String, accountName: String, items: JSONArray): List<Meeting> = (0 until items.length()).mapNotNull { i ->
        val o = items.getJSONObject(i)
        val start = runCatching { OffsetDateTime.parse(o.getString("start_iso")).toInstant().toEpochMilli() }.getOrNull()
            ?: return@mapNotNull null
        Meeting(accountId, accountName, o.getString("item_id"),
            o.optString("subject").replace(forwardPrefix, "").trim().ifBlank { "(без темы)" },
            start, o.optString("meeting_status").equals("cancelled", true), o.optBoolean("is_all_day"),
            joinUrl(o.optString("location").ifBlank { null }, o.optString("body").ifBlank { null }))
    }

    private val hosts = listOf("teams.microsoft", "teams.live", "zoom.us", "ktalk", "kontur",
        "meet.google", "trueconf", "jazz.sber", "telemost.yandex", "webex.com")
    private val urlRe = Regex("""https?://[^\s<>'")\]]+""")
    private val wholeUrl = Regex("""^https?://\S+$""")

    fun joinUrl(location: String?, body: String?): String? {
        location?.trim()?.let { if (wholeUrl.matches(it)) return it }
        val urls = listOfNotNull(location, body).flatMap { t ->
            val norm = t.replace("\\/", "/").replace("&amp;", "&")
            linkedSetOf(t, norm, norm.replace(Regex("<[^>]+>"), " "))
        }.flatMap { urlRe.findAll(it).map { m -> m.value.trimEnd('.', ',', ';', ')', ']') } }
        return urls.firstOrNull { u -> hosts.any { u.substringAfter("://").substringBefore('/').lowercase().contains(it) } }
            ?: urls.firstOrNull()
    }
}
