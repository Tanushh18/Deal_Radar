package com.dealradar.app

import android.content.Context
import android.net.Uri

/**
 * Where the DealRadar backend lives, persisted across launches.
 *
 * The app ships without a hardcoded host on purpose: the same APK has to work
 * against an emulator's host loopback (10.0.2.2), a laptop on the same Wi-Fi,
 * and a deployed https:// instance.
 */
object ServerConfig {

    /** The deployed instance — what the app points at unless told otherwise. */
    const val LIVE_HOST = "https://dealradar-k2hb.onrender.com"
    const val EMULATOR_HOST = "http://10.0.2.2:8000"

    private const val PREFS = "dealradar_prefs"
    private const val KEY_BASE_URL = "base_url"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun baseUrl(context: Context): String? =
        prefs(context).getString(KEY_BASE_URL, null)?.takeIf { it.isNotBlank() }

    fun save(context: Context, url: String) {
        prefs(context).edit().putString(KEY_BASE_URL, normalize(url)).apply()
    }

    fun clear(context: Context) {
        prefs(context).edit().remove(KEY_BASE_URL).apply()
    }

    /**
     * Turns what someone actually types ("10.0.2.2:8000", "localhost:8000/",
     * "https://deals.example.com") into a usable origin.
     *
     * "localhost" is rewritten to 10.0.2.2 because on a device — emulator or
     * physical — localhost is the phone itself, so a server running on the dev
     * machine is unreachable at that name. Typing it is a near-universal first
     * mistake, and the failure it produces (connection refused) gives no hint
     * as to why.
     */
    fun normalize(raw: String): String {
        var url = raw.trim().trimEnd('/')
        if (url.isEmpty()) return url
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            // A bare hostname gets https, because guessing http against a real
            // deployment is not a harmless guess: the backend marks the session
            // cookie Secure, and a Secure cookie sent over http is dropped —
            // sign-in would appear to work and then instantly log you out.
            // Local addresses are the exception; those genuinely serve plain http.
            url = if (isLocalAddress(url.substringBefore('/').substringBefore(':'))) {
                "http://$url"
            } else {
                "https://$url"
            }
        }
        val uri = Uri.parse(url)
        val host = uri.host
        if (host == "localhost" || host == "127.0.0.1") {
            val port = if (uri.port > 0) ":${uri.port}" else ""
            url = "${uri.scheme}://10.0.2.2$port"
        }
        return url.trimEnd('/')
    }

    /** Loopback, private LAN ranges, and .local — the addresses served over http. */
    private fun isLocalAddress(host: String): Boolean {
        if (host.equals("localhost", ignoreCase = true) || host.endsWith(".local")) return true
        if (host.startsWith("10.") || host.startsWith("127.") || host.startsWith("192.168.")) return true
        // 172.16.0.0 – 172.31.255.255
        val second = host.removePrefix("172.").substringBefore('.').toIntOrNull()
        return host.startsWith("172.") && second != null && second in 16..31
    }

    /** True when [url] belongs to the configured server, so it stays in-app. */
    fun isOwnHost(base: String, url: String): Boolean {
        val baseHost = Uri.parse(base).host ?: return false
        val targetHost = Uri.parse(url).host ?: return false
        return baseHost.equals(targetHost, ignoreCase = true)
    }
}
