package ru.olesyaba.aasmail

import android.content.Context
import android.util.Base64
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
                Python.getInstance().getModule("android_entry")
                    .callAttr("run", app.filesDir.path, app.cacheDir.path, pageKey)
            }, "webapp").apply { isDaemon = true }.start()
            started = true
        }
        repeat(150) { if (alive()) return; Thread.sleep(200) }
        throw IllegalStateException("webapp did not start")
    }

    fun token(ctx: Context): String =
        File(ctx.filesDir, "eas-bridge/runtime_token").readText().trim()

    private fun alive() = try {
        (URL("$BASE/api/about").openConnection() as HttpURLConnection).run {
            connectTimeout = 300; readTimeout = 300; requestMethod = "POST"; doOutput = true
            outputStream.close(); responseCode; disconnect(); true   // 403 without a token is still "up"
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
