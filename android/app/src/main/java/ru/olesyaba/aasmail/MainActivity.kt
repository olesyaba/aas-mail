package ru.olesyaba.aasmail

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.webkit.JsResult
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import java.io.IOException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLDecoder
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {
    companion object { const val EXTRA_OPEN = "open" }
    lateinit var web: WebView
    private var fileCb: ValueCallback<Array<Uri>>? = null
    private val pickFiles = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        fileCb?.onReceiveValue(uris.toTypedArray()); fileCb = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Permissions.ask(this)
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest): Boolean {
                if (Links.isOurs(r.url.toString())) return false
                try { startActivity(Intent(Intent.ACTION_VIEW, r.url)) }   // mail links, meeting links → other apps
                catch (e: ActivityNotFoundException) { Toast.makeText(this@MainActivity, "Нет приложения для этой ссылки", Toast.LENGTH_SHORT).show() }
                return true
            }
            // The page embeds X-Tok: fetch it ourselves with the page key (see webapp.PAGE_KEY).
            override fun shouldInterceptRequest(v: WebView, r: WebResourceRequest): WebResourceResponse? {
                val u = r.url
                if (!Links.isOurs(u.toString()) || (u.path ?: "/") !in setOf("/", "/index.html")) return null
                return try {
                    val c = URL(u.toString()).openConnection() as HttpURLConnection
                    c.setRequestProperty("X-Page-Key", PyServer.pageKey)
                    val code = c.responseCode
                    WebResourceResponse("text/html", "utf-8", code, c.responseMessage ?: "OK",
                        c.headerFields.filterKeys { it != null }.mapValues { it.value.joinToString(",") },
                        if (code >= 400) c.errorStream else c.inputStream)
                } catch (e: IOException) {
                    WebResourceResponse("text/plain", "utf-8", 503, "Unavailable", emptyMap(),
                        "Почтовый сервер не отвечает — перезапустите приложение".byteInputStream())
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            // Without these Android WebView cancels confirm()/alert() and ignores <input type=file>.
            override fun onJsAlert(v: WebView, url: String, msg: String, r: JsResult): Boolean {
                AlertDialog.Builder(this@MainActivity).setMessage(msg).setPositiveButton("OK") { _, _ -> r.confirm() }
                    .setOnCancelListener { r.cancel() }.show(); return true
            }
            override fun onJsConfirm(v: WebView, url: String, msg: String, r: JsResult): Boolean {
                AlertDialog.Builder(this@MainActivity).setMessage(msg)
                    .setPositiveButton("OK") { _, _ -> r.confirm() }.setNegativeButton("Отмена") { _, _ -> r.cancel() }
                    .setOnCancelListener { r.cancel() }.show(); return true
            }
            override fun onShowFileChooser(v: WebView, cb: ValueCallback<Array<Uri>>, p: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null); fileCb = cb
                pickFiles.launch(arrayOf("*/*")); return true
            }
        }
        val attachments = Attachments(this)
        web.addJavascriptInterface(NativeBridge(this, attachments), "AASNative")
        // The web UI talks to the Mac shell through window.webkit.messageHandlers: route that to AASNative.
        val shim = "window.webkit={messageHandlers:new Proxy({},{get:(_,n)=>({postMessage:v=>AASNative.post(String(n),JSON.stringify(v===undefined?null:v))})})};"
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT))
            WebViewCompat.addDocumentStartJavaScript(web, shim, setOf("http://127.0.0.1:8780"))
        web.setDownloadListener { url, _, disposition, _, _ ->
            val name = Regex("filename\\*=UTF-8''([^;]+)").find(disposition ?: "")?.groupValues?.get(1)
                ?.let { URLDecoder.decode(it, "UTF-8") } ?: Uri.parse(url).lastPathSegment ?: "attachment"
            attachments.handle("as", JSONArray().put(JSONObject().put("url", url).put("name", name)))
        }
        Notifier.channels(this)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })
        setContentView(TextView(this).apply { text = "AAS mail запускается…"; setPadding(48, 96, 48, 48) })
        thread {
            try {
                PyServer.ensureStarted(this)
                SyncWorker.schedule(this); SyncWorker.runNow(this)
                runOnUiThread { setContentView(web); web.loadUrl(startUrl(intent)) }
            } catch (e: Exception) {
                runOnUiThread { showStartError() }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val open = intent.getStringExtra(EXTRA_OPEN) ?: return
        val (acct, id) = open.split(":", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        web.evaluateJavascript("window.aasOpen && aasOpen(${JSONObject.quote(acct)}, ${JSONObject.quote(id)})", null)
    }

    private fun startUrl(i: Intent?) =
        PyServer.BASE + "/" + (i?.getStringExtra(EXTRA_OPEN)?.let { "#open=" + Uri.encode(it) } ?: "")

    private fun showStartError() {
        val log = File(filesDir, "eas-bridge/eas-mail.log")
        setContentView(TextView(this).apply {
            text = "Не удалось запустить почтовый сервер.\n\nНажмите, чтобы отправить лог."
            setPadding(48, 96, 48, 48)
            setOnClickListener {
                val send = Intent(Intent.ACTION_SEND).setType("text/plain")
                    .putExtra(Intent.EXTRA_TEXT, if (log.exists()) log.readText().takeLast(20_000) else "лога нет")
                startActivity(Intent.createChooser(send, "Отправить лог"))
            }
        })
    }
}
