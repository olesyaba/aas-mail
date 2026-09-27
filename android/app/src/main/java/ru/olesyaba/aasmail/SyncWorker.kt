package ru.olesyaba.aasmail

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.time.LocalDate
import java.util.concurrent.TimeUnit

/** Every 15 minutes (Android's floor): new Inbox mail → notification, next 24–48 h of meetings → alarms. */
class SyncWorker(ctx: Context, p: WorkerParameters) : Worker(ctx, p) {
    companion object {
        private val online = Constraints(requiredNetworkType = NetworkType.CONNECTED)
        fun schedule(ctx: Context) = WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("sync",
            ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).setConstraints(online).build())
        fun runNow(ctx: Context) = WorkManager.getInstance(ctx).enqueue(
            OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(online).build())
    }

    override fun doWork(): Result {
        val ctx = applicationContext
        try { PyServer.ensureStarted(ctx) } catch (e: Exception) { return Result.retry() }
        val st = ctx.getSharedPreferences("sync", Context.MODE_PRIVATE)
        val accounts = try { Api.post(ctx, "/api/accounts", JSONObject()).getJSONArray("accounts") } catch (e: IOException) { return Result.retry() }
        val lead = runCatching { Api.post(ctx, "/api/prefs", JSONObject()).getJSONObject("prefs").optInt("reminder_minutes", 5) }.getOrDefault(5)
        val meetings = mutableListOf<Meeting>()
        val today = LocalDate.now()
        for (i in 0 until accounts.length()) {
            val a = accounts.getJSONObject(i)
            val id = a.getString("id"); val name = a.getString("name")
            if (!a.isNull("auth_error")) continue  // wrong password: the UI already says so
            try {
                val r = Api.post(ctx, "/api/mail", JSONObject().put("action", "list").put("limit", 20).put("acct", id))
                if (!r.optBoolean("ok", true)) throw IOException(r.optString("error"))
                val known = st.getStringSet("known-$id", null)
                val (fresh, next) = NewMail.diff(NewMail.parse(r.getJSONArray("items")), known)
                st.edit().putStringSet("known-$id", next).putInt("fail-$id", 0).apply()
                Notifier.newMail(ctx, id, name, fresh)
                val ev = Api.post(ctx, "/api/events", JSONObject().put("action", "list").put("acct", id)
                    .put("start", today.toString()).put("end", today.plusDays(2).toString()).put("limit", 300))
                if (ev.optBoolean("ok")) meetings += MeetingPlan.parse(id, name, ev.getJSONArray("items"))
            } catch (e: Exception) {
                if (e !is IOException && e !is JSONException) throw e
                val n = st.getInt("fail-$id", 0) + 1
                st.edit().putInt("fail-$id", n).apply()
                if (n == 3) Notifier.unreachable(ctx, name)
            }
        }
        MeetingAlarms.reschedule(ctx, MeetingPlan.plan(meetings, System.currentTimeMillis(), lead))
        return Result.success()
    }
}
