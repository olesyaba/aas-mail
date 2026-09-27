package ru.olesyaba.aasmail

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object Notifier {
    private const val MAIL = "mail"; private const val MEET = "meetings"; private const val STATUS = "status"

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(MAIL, "Новые письма", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(MEET, "Встречи", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(STATUS, "Связь с сервером", NotificationManager.IMPORTANCE_LOW))
    }

    private fun open(ctx: Context, req: Int, extra: String?) = PendingIntent.getActivity(ctx, req,
        Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .apply { extra?.let { putExtra(MainActivity.EXTRA_OPEN, it) } },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun post(ctx: Context, id: Int, n: NotificationCompat.Builder) {
        channels(ctx)  // idempotent; the worker may run before the UI ever did
        try { NotificationManagerCompat.from(ctx).notify(id, n.build()) } catch (_: SecurityException) { /* permission denied */ }
    }

    fun newMail(ctx: Context, acctId: String, acctName: String, items: List<MailItem>) {
        if (items.isEmpty()) return
        val marker = if (acctId == "seller") "🟢" else "🔴"
        val top = items.first()
        val n = NotificationCompat.Builder(ctx, MAIL).setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(if (items.size == 1) "$marker ${top.from}" else "$marker $acctName: ${items.size} новых")
            .setContentText(top.subject).setNumber(items.size).setAutoCancel(true)
            .setGroup("mail-$acctId")
            .setContentIntent(open(ctx, acctId.hashCode(), "$acctId:${top.itemId}"))
        post(ctx, "mail-$acctId".hashCode(), n)
    }

    fun meeting(ctx: Context, r: Reminder) {
        val n = NotificationCompat.Builder(ctx, MEET).setSmallIcon(android.R.drawable.ic_menu_my_calendar)
            .setContentTitle(r.title).setContentText(r.body).setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_EVENT).setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open(ctx, r.key.hashCode(), null))
        r.joinUrl?.let {
            n.addAction(0, "Подключиться", PendingIntent.getActivity(ctx, r.key.hashCode() + 1,
                Intent(Intent.ACTION_VIEW, Uri.parse(it)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE))
        }
        post(ctx, r.key.hashCode(), n)
    }

    fun unreachable(ctx: Context, acctName: String) {
        post(ctx, "net-$acctName".hashCode(), NotificationCompat.Builder(ctx, STATUS)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Нет связи с $acctName")
            .setContentText("Проверьте сеть или VPN. Почта обновится сама, когда связь появится.")
            .setAutoCancel(true).setContentIntent(open(ctx, 7, null)))
    }
}
