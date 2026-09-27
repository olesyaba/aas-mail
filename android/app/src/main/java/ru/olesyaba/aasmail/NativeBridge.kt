package ru.olesyaba.aasmail

import android.webkit.JavascriptInterface
import org.json.JSONObject

/** Receives what the web UI posts to the Mac shell (window.webkit.messageHandlers.<name>). */
class NativeBridge(private val a: MainActivity, private val attachments: Attachments) {
    @JavascriptInterface
    fun post(name: String, json: String) {
        when (name) {
            "aasSave" -> JSONObject(json).let { p -> a.runOnUiThread { attachments.handle(p.optString("mode"), p.getJSONArray("files")) } }
            // The open app notifies like the tray does; the background worker covers the closed app.
            "aasNewMail" -> JSONObject(json).let { p ->
                val top = MailItem(p.optString("item_id"), false, p.optString("from"), p.optString("subject"))
                Notifier.newMail(a, p.optString("acct", "main"), p.optString("account"),
                    List(p.optInt("count", 1).coerceAtLeast(1)) { top })
            }
            else -> Unit  // aasTheme/aasPalette/aasPrefs/aasBadge/aasPlaySound: Mac-only (tray, Dock, sounds)
        }
    }
}
