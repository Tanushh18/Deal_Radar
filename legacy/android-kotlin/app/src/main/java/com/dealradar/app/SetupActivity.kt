package com.dealradar.app

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.dealradar.app.databinding.ActivitySetupBinding
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * First-run (and "change server") screen: point the app at a DealRadar backend
 * and confirm it actually answers before committing to it.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySetupBinding
    private val io = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var ticker: Runnable? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ServerConfig.baseUrl(this)?.let { binding.urlInput.setText(it) }

        binding.chipLive.setOnClickListener {
            binding.urlInput.setText(ServerConfig.LIVE_HOST)
        }
        binding.chipEmulator.setOnClickListener {
            binding.urlInput.setText(ServerConfig.EMULATOR_HOST)
        }
        binding.chipLan.setOnClickListener {
            // Only the prefix is knowable — the last octet is theirs to fill in.
            binding.urlInput.setText("http://192.168.")
            binding.urlInput.setSelection(binding.urlInput.text?.length ?: 0)
        }

        binding.btnConnect.setOnClickListener { testAndSave() }
        binding.btnSaveAnyway.setOnClickListener {
            val url = ServerConfig.normalize(binding.urlInput.text.toString())
            if (url.isEmpty()) {
                status(getString(R.string.error_title), isError = true)
            } else {
                commit(url)
            }
        }
    }

    private fun testAndSave() {
        val url = ServerConfig.normalize(binding.urlInput.text.toString())
        if (url.isEmpty()) {
            status("Enter your server address first.", isError = true)
            return
        }
        binding.urlInput.setText(url)
        setBusy(true)
        startElapsedTicker()

        io.execute {
            val result = probe(url)
            runOnUiThread {
                stopElapsedTicker()
                setBusy(false)
                if (result == null) {
                    status(getString(R.string.setup_ok), isError = false)
                    commit(url)
                } else {
                    // Never a dead end: the check is advisory, and the WebView
                    // is the real test anyway, so offer a way through it.
                    status("$result\n\nTap “${getString(R.string.setup_save_anyway)}” to open it regardless.", isError = true)
                }
            }
        }
    }

    /**
     * Counts seconds while the probe runs.
     *
     * A silent "Checking server…" that sits for the whole timeout is
     * indistinguishable from a frozen app — the first version of this screen
     * looked hung for exactly that reason.
     */
    private fun startElapsedTicker() {
        val startedAt = System.currentTimeMillis()
        ticker = object : Runnable {
            override fun run() {
                val secs = (System.currentTimeMillis() - startedAt) / 1000
                status("${getString(R.string.setup_testing)}  (${secs}s)", isError = false)
                handler.postDelayed(this, 1000)
            }
        }
        handler.post(ticker!!)
    }

    private fun stopElapsedTicker() {
        ticker?.let { handler.removeCallbacks(it) }
        ticker = null
    }

    /** Returns null on success, or a human-readable reason it failed. */
    private fun probe(base: String): String? {
        return try {
            val connection = (URL("$base/api/ping").openConnection() as HttpURLConnection).apply {
                // Long enough to cover a free-tier cold start, short enough that
                // a genuinely unreachable server reports back while the user is
                // still watching. Paired with the on-screen seconds counter so
                // the wait is always visibly progressing.
                connectTimeout = 20_000
                readTimeout = 25_000
                requestMethod = "GET"
                setRequestProperty("Accept", "application/json")
            }
            try {
                val code = connection.responseCode
                if (code != 200) {
                    return "Server answered with HTTP $code. Is that the DealRadar address?"
                }
                val body = connection.inputStream.bufferedReader().use { it.readText() }
                val service = JSONObject(body).optString("service")
                if (service != "dealradar") {
                    "Something is running there, but it isn't DealRadar."
                } else {
                    null
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            "Couldn't reach $base — ${e.message ?: e.javaClass.simpleName}.\n\n" +
                "If it's the live server, check you're online and try again. " +
                "If it's a local one, check it's running (make android), bound to " +
                "0.0.0.0, and that this device is on the same network."
        }
    }

    private fun commit(url: String) {
        ServerConfig.save(this, url)
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
        finish()
    }

    private fun setBusy(busy: Boolean) {
        binding.btnConnect.isEnabled = !busy
        binding.btnSaveAnyway.isEnabled = !busy
        binding.urlInput.isEnabled = !busy
    }

    private fun status(message: String, isError: Boolean) {
        binding.statusText.visibility = View.VISIBLE
        binding.statusText.text = message
        binding.statusText.setTextColor(
            getColor(if (isError) R.color.hot else R.color.good)
        )
    }

    override fun onDestroy() {
        stopElapsedTicker()
        io.shutdownNow()
        super.onDestroy()
    }
}
