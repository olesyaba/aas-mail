package ru.olesyaba.aasmail

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Exact reminders; the set of scheduled keys lives in prefs so stale ones are cancelled. */
object MeetingAlarms {
    private const val PREFS = "alarms"

    private fun pi(ctx: Context, r: Reminder?, key: String) = PendingIntent.getBroadcast(ctx, key.hashCode(),
        Intent(ctx, MeetingReceiver::class.java).apply {
            putExtra("key", key)
            r?.let { putExtra("title", it.title); putExtra("body", it.body); putExtra("join", it.joinUrl); putExtra("at", it.fireAt) }
        }, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** Keys to cancel: gone or moved meetings of accounts that synced. A failed account
     *  (no VPN, calendar still loading) keeps its reminders instead of silently losing them. */
    fun stale(old: Set<String>, want: Set<String>, synced: Set<String>): Set<String> =
        old.filter { k -> k !in want && synced.any { k.startsWith("$it-") } }.toSet()

    fun reschedule(ctx: Context, reminders: List<Reminder>, synced: Set<String>) {
        val am = ctx.getSystemService(AlarmManager::class.java)
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val old = p.getStringSet("keys", emptySet())!!
        val want = reminders.associateBy { it.key }
        val gone = stale(old, want.keys, synced)
        gone.forEach { am.cancel(pi(ctx, null, it)) }
        want.values.forEach { r ->
            val op = pi(ctx, r, r.key)
            if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.fireAt, op)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.fireAt, op)  // no permission: a few minutes late
        }
        p.edit().putStringSet("keys", old - gone + want.keys).apply()
    }
}

class MeetingReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        Notifier.meeting(ctx, Reminder(i.getStringExtra("key") ?: return, i.getLongExtra("at", 0),
            i.getStringExtra("title").orEmpty(), i.getStringExtra("body").orEmpty(), i.getStringExtra("join")))
    }
}

class BootReceiver : BroadcastReceiver() {
    // Alarms die on reboot; the next sync re-plans them, so just sync now.
    override fun onReceive(ctx: Context, i: Intent) {
        if (i.action == Intent.ACTION_BOOT_COMPLETED) SyncWorker.runNow(ctx)
    }
}
