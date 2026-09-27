package ru.olesyaba.aasmail

import java.net.URI

object Links {
    /** Only our own server: any other 127.0.0.1 port may be another app on the phone. */
    fun isOurs(url: String): Boolean = runCatching {
        val u = URI(url)
        u.scheme == "http" && u.host == "127.0.0.1" && u.port == 8780
    }.getOrDefault(false)
}
