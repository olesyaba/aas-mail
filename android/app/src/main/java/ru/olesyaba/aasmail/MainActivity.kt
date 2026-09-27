package ru.olesyaba.aasmail

import android.annotation.SuppressLint
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

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest): Boolean {
                if (r.url.host == "127.0.0.1") return false
                startActivity(Intent(Intent.ACTION_VIEW, r.url))   // mail links, meeting links → other apps
                return true
            }
            // The page embeds X-Tok: fetch it ourselves with the page key (see webapp.PAGE_KEY).
            override fun shouldInterceptRequest(v: WebView, r: WebResourceRequest): WebResourceResponse? {
                val u = r.url
                if (u.host != "127.0.0.1" || (u.path ?: "/") !in setOf("/", "/index.html")) return null
                val c = URL(u.toString()).openConnection() as HttpURLConnection
                c.setRequestProperty("X-Page-Key", PyServer.pageKey)
                val code = c.responseCode
                return WebResourceResponse("text/html", "utf-8", code, c.responseMessage ?: "OK",
                    c.headerFields.filterKeys { it != null }.mapValues { it.value.joinToString(",") },
                    if (code >= 400) c.errorStream else c.inputStream)
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
