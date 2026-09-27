package ru.olesyaba.aasmail

import android.Manifest
import android.app.AlarmManager
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts

/** First run: notifications, exact alarms, no battery optimisation (One UI puts idle apps to sleep). */
object Permissions {
    fun ask(a: ComponentActivity) {
        val notif = a.registerForActivityResult(ActivityResultContracts.RequestPermission()) { next(a) }
        notif.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun next(a: ComponentActivity) {
        val am = a.getSystemService(AlarmManager::class.java)
        val pm = a.getSystemService(PowerManager::class.java)
        val steps = buildList {
            if (!am.canScheduleExactAlarms()) add("Точные напоминания о встречах" to
                Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${a.packageName}")))
            if (!pm.isIgnoringBatteryOptimizations(a.packageName)) add("Проверка почты в фоне" to
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${a.packageName}")))
        }
        val (title, intent) = steps.firstOrNull() ?: return
        AlertDialog.Builder(a).setTitle(title)
            .setMessage("Без этого разрешения уведомления будут приходить с опозданием или не придут.")
            .setPositiveButton("Разрешить") { _, _ -> a.startActivity(intent) }
            .setNegativeButton("Позже", null).show()
    }
}
