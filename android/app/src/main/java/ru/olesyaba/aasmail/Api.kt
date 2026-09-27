package ru.olesyaba.aasmail

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** POST /api/<route> with X-Tok — the Kotlin twin of app/TrayAPIClient.swift. */
object Api {
    fun post(ctx: Context, path: String, body: JSONObject): JSONObject {
        val c = URL(PyServer.BASE + path).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 5_000; c.readTimeout = 90_000
            c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("X-Tok", PyServer.token(ctx))
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            if (c.responseCode != 200) throw IOException("HTTP ${c.responseCode} for $path")
            return JSONObject(c.inputStream.bufferedReader().readText())
        } finally {
            c.disconnect()
        }
    }
}
