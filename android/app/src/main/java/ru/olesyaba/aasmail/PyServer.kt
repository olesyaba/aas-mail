package ru.olesyaba.aasmail

import android.content.Context
import android.util.Base64
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom

/** The shared webapp.py, running in a thread of this process (like the Mac app's bundled server). */
object PyServer {
    const val BASE = "http://127.0.0.1:8780"
    val pageKey: String = Base64.encodeToString(ByteArray(24).also { SecureRandom().nextBytes(it) },
        Base64.NO_WRAP or Base64.URL_SAFE)
    @Volatile private var started = false

    @Synchronized fun ensureStarted(ctx: Context) {
        val app = ctx.applicationContext
        if (!started) {
            Secrets.init(app)
            copyWeb(app)
            if (!Python.isStarted()) Python.start(AndroidPlatform(app))
            Thread({
                try {
                    Python.getInstance().getModule("android_entry")
                        .callAttr("run", app.filesDir.path, app.cacheDir.path, pageKey)
                } catch (e: Throwable) {  // port busy, broken config, import error: show the error screen, don't crash
                    Log.e("AASMail", "webapp stopped", e)
                    runCatching { File(app.filesDir, "eas-bridge/eas-mail.log").appendText("\nwebapp stopped: $e\n") }
                }
            }, "webapp").apply { isDaemon = true }.start()
            started = true
        }
        repeat(150) { if (alive(app)) return; Thread.sleep(200) }
        throw IllegalStateException("webapp did not start")
    }

    fun token(ctx: Context): String =
        File(ctx.filesDir, "eas-bridge/runtime_token").readText().trim()

    /** Up = answers with OUR token: another app squatting on 8780 cannot know it (webapp writes it before binding). */
    private fun alive(ctx: Context) = try {
        (URL("$BASE/api/about").openConnection() as HttpURLConnection).run {
            connectTimeout = 300; readTimeout = 1000; requestMethod = "POST"; doOutput = true
            setRequestProperty("X-Tok", token(ctx))
            outputStream.use { it.write("{}".toByteArray()) }
            (responseCode == 200).also { disconnect() }
        }
    } catch (e: Exception) { false }

    /** assets/web → filesDir/web once per APK install/update (webapp.WEB points there). */
    private fun copyWeb(ctx: Context) {
        val stamp = File(ctx.filesDir, "web/.version")
        val ver = ctx.packageManager.getPackageInfo(ctx.packageName, 0).lastUpdateTime.toString()
        if (stamp.exists() && stamp.readText() == ver) return
        File(ctx.filesDir, "web").deleteRecursively()
        fun copy(path: String) {
            val kids = ctx.assets.list(path) ?: emptyArray()
            if (kids.isEmpty()) {
                val out = File(ctx.filesDir, path).apply { parentFile?.mkdirs() }
                ctx.assets.open(path).use { i -> out.outputStream().use { i.copyTo(it) } }
            } else kids.forEach { copy("$path/$it") }
        }
        copy("web")
        stamp.writeText(ver)
    }
}
