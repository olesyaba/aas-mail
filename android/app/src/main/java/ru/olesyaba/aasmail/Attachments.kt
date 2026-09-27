package ru.olesyaba.aasmail

import android.content.Intent
import android.net.Uri
import android.webkit.MimeTypeMap
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL
import kotlin.concurrent.thread

/** «Открыть» / «Сохранить как…» / «Сохранить все…» — the Mac shell's aasSave, via the Storage Access Framework. */
class Attachments(private val a: MainActivity) {
    private var pending: List<Pair<String, String>> = emptyList()  // (url, name)

    private val saveOne = a.registerForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri ->
        uri?.let { u -> io { write(pending.first().first, u); "Сохранено: ${pending.first().second}" } }
    }
    private val saveAll = a.registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { tree ->
        tree ?: return@registerForActivityResult
        io {
            val dir = DocumentFile.fromTreeUri(a, tree)!!
            pending.forEach { (url, name) ->
                val f = dir.createFile(mime(name), name) ?: error("не удалось создать $name")
                write(url, f.uri)
            }
            "Сохранено файлов: ${pending.size}"
        }
    }

    fun handle(mode: String, files: JSONArray) {
        pending = (0 until files.length()).map { files.getJSONObject(it).let { f -> abs(f.getString("url")) to f.getString("name") } }
        if (pending.isEmpty()) return
        when (mode) {
            "as" -> saveOne.launch(pending.first().second)
            "all" -> saveAll.launch(null)
            else -> io {  // open
                val (url, name) = pending.first()
                val f = File(a.cacheDir, "att/${File(name).name}").apply { parentFile?.mkdirs() }
                URL(url).openStream().use { i -> f.outputStream().use { i.copyTo(it) } }
                val uri = FileProvider.getUriForFile(a, "ru.olesyaba.aasmail.files", f)
                a.startActivity(Intent.createChooser(Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime(name))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), name))
                null
            }
        }
    }

    // Only our own server: a URL from the page must never make the shell fetch elsewhere.
    private fun abs(u: String) = if (u.startsWith("/")) PyServer.BASE + u
        else u.also { require(it.startsWith(PyServer.BASE + "/")) { "foreign url" } }
    private fun mime(name: String) =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase()) ?: "application/octet-stream"
    private fun write(url: String, to: Uri) {
        URL(url).openStream().use { i -> a.contentResolver.openOutputStream(to)!!.use { i.copyTo(it) } }
    }
    private fun io(job: () -> String?) = thread {
        val (ok, text) = try { true to job() } catch (e: Exception) { false to "Не удалось сохранить вложение: ${e.message}" }
        text?.let { t -> a.runOnUiThread { a.web.evaluateJavascript("window.aasSaved && aasSaved($ok, ${JSONObject.quote(t)})", null) } }
    }
}
