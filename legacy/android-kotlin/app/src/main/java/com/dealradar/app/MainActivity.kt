package com.dealradar.app

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Message
import android.view.View
import android.webkit.CookieManager
import android.webkit.DownloadListener
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.dealradar.app.databinding.ActivityMainBinding

/**
 * The app shell: a full-bleed WebView onto the DealRadar frontend, plus the
 * native behaviour a browser tab would otherwise supply — back navigation,
 * pull to refresh, external links, downloads, and a connection-error state.
 *
 * The UI itself is served by the backend rather than bundled into the APK.
 * That is what keeps this "the same app": the session cookie, the fetch calls
 * and the page are all one origin, so nothing has to be reimplemented or
 * proxied, and a server-side UI fix ships without a new APK.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var baseUrl: String

    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var lastBackPress = 0L
    private var lastScrollY = 0
    private var pullToRefreshAllowed = true

    private val filePicker = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = fileChooserCallback ?: return@registerForActivityResult
        fileChooserCallback = null
        callback.onReceiveValue(
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Drop the splash window background now that real content is coming.
        setTheme(R.style.Theme_DealRadar)

        val configured = ServerConfig.baseUrl(this)
        if (configured == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        baseUrl = configured

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        configureWebView()
        wireControls()
        registerBackHandler()

        if (savedInstanceState != null) {
            binding.webView.restoreState(savedInstanceState)
        } else {
            binding.webView.loadUrl(baseUrl)
        }
    }

    /** Reached when SetupActivity relaunches us after the server URL changes. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val configured = ServerConfig.baseUrl(this) ?: return
        if (configured != baseUrl) {
            baseUrl = configured
            showError(null)
            binding.webView.loadUrl(baseUrl)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() = with(binding.webView) {
        settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            // "Buy now" and the trending cards are target="_blank" anchors;
            // without multiple-window support the WebView silently swallows
            // them, so the primary action of the whole app would do nothing.
            setSupportMultipleWindows(true)
            javaScriptCanOpenWindowsAutomatically = true
            userAgentString = "$userAgentString DealRadarAndroid/${BuildConfig.VERSION_NAME}"
        }

        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(this@with, true)
        }

        addJavascriptInterface(
            WebAppBridge(::openSettings, ::setPullToRefreshAllowed),
            "DealRadarNative"
        )

        webViewClient = DealRadarWebViewClient()
        webChromeClient = DealRadarChromeClient()

        setDownloadListener(DownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            startDownload(url, userAgent, contentDisposition, mimeType)
        })

        // Pull-to-refresh must not fight the page's own scrolling: arm it only
        // when the content is already at the top, and never while the page has
        // a bottom sheet open (see WebAppBridge.setPullToRefresh).
        setOnScrollChangeListener { _, _, scrollY, _, _ ->
            lastScrollY = scrollY
            syncPullToRefresh()
        }
    }

    /** Called from the page (any thread) whenever a sheet opens or closes. */
    private fun setPullToRefreshAllowed(allowed: Boolean) {
        runOnUiThread {
            pullToRefreshAllowed = allowed
            syncPullToRefresh()
        }
    }

    private fun syncPullToRefresh() {
        binding.swipeRefresh.isEnabled = pullToRefreshAllowed && lastScrollY == 0
    }

    private fun wireControls() {
        binding.swipeRefresh.setColorSchemeResources(R.color.accent)
        binding.swipeRefresh.setProgressBackgroundColorSchemeResource(R.color.surface)
        binding.swipeRefresh.setOnRefreshListener {
            showError(null)
            binding.webView.reload()
        }

        binding.btnRetry.setOnClickListener {
            showError(null)
            binding.webView.loadUrl(baseUrl)
        }
        binding.btnChangeServer.setOnClickListener { openSettings() }
    }

    private fun registerBackHandler() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.webView.canGoBack()) {
                    binding.webView.goBack()
                    return
                }
                val now = System.currentTimeMillis()
                if (now - lastBackPress < 2000) {
                    finish()
                } else {
                    lastBackPress = now
                    Toast.makeText(
                        this@MainActivity, R.string.back_again_to_exit, Toast.LENGTH_SHORT
                    ).show()
                }
            }
        })
    }

    private fun openSettings() {
        runOnUiThread { startActivity(Intent(this, SetupActivity::class.java)) }
    }

    private fun showError(message: String?) {
        binding.errorView.visibility = if (message == null) View.GONE else View.VISIBLE
        binding.swipeRefresh.visibility = if (message == null) View.VISIBLE else View.GONE
        binding.errorDetail.text = message ?: ""
        binding.swipeRefresh.isRefreshing = false
    }

    private fun openExternally(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "Nothing installed can open that link.", Toast.LENGTH_SHORT).show()
        }
    }

    private fun startDownload(url: String, userAgent: String, disposition: String, mime: String) {
        try {
            val request = DownloadManager.Request(Uri.parse(url)).apply {
                setMimeType(mime)
                addRequestHeader("User-Agent", userAgent)
                addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setDestinationInExternalFilesDir(
                    this@MainActivity,
                    android.os.Environment.DIRECTORY_DOWNLOADS,
                    android.webkit.URLUtil.guessFileName(url, disposition, mime)
                )
            }
            (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            Toast.makeText(this, "Downloading…", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            openExternally(url)
        }
    }

    private inner class DealRadarWebViewClient : WebViewClient() {

        override fun shouldOverrideUrlLoading(
            view: WebView,
            request: WebResourceRequest
        ): Boolean {
            val url = request.url.toString()
            val scheme = request.url.scheme?.lowercase()

            // Anything that isn't ordinary web traffic (tg://, mailto:, intent://,
            // upi://) belongs to another app, not to this WebView.
            if (scheme != "http" && scheme != "https") {
                openExternally(url)
                return true
            }
            if (ServerConfig.isOwnHost(baseUrl, url)) return false

            openExternally(url)
            return true
        }

        override fun onPageFinished(view: WebView, url: String) {
            binding.swipeRefresh.isRefreshing = false
            view.evaluateJavascript(NATIVE_GLUE_JS, null)
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError
        ) {
            // Subresource failures (a dead product image, a blocked tracker)
            // must not blank out a page that otherwise loaded fine.
            if (!request.isForMainFrame) return
            showError("${error.description}\n\n$baseUrl")
        }
    }

    private inner class DealRadarChromeClient : WebChromeClient() {

        override fun onProgressChanged(view: WebView, newProgress: Int) {
            binding.progressBar.progress = newProgress
            binding.progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
        }

        /**
         * target="_blank" / window.open. The requested URL isn't handed over
         * directly, so the standard dance is to hand back a throwaway WebView
         * purely to learn the destination, then route it to a real browser.
         */
        override fun onCreateWindow(
            view: WebView,
            isDialog: Boolean,
            isUserGesture: Boolean,
            resultMsg: Message
        ): Boolean {
            val probe = WebView(view.context)
            probe.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    probeView: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    val url = request.url.toString()
                    if (ServerConfig.isOwnHost(baseUrl, url)) {
                        binding.webView.loadUrl(url)
                    } else {
                        openExternally(url)
                    }
                    probeView.destroy()
                    return true
                }
            }
            (resultMsg.obj as WebView.WebViewTransport).webView = probe
            resultMsg.sendToTarget()
            return true
        }

        override fun onShowFileChooser(
            webView: WebView,
            filePathCallback: ValueCallback<Array<Uri>>,
            fileChooserParams: FileChooserParams
        ): Boolean {
            fileChooserCallback?.onReceiveValue(null)
            fileChooserCallback = filePathCallback
            return try {
                filePicker.launch(fileChooserParams.createIntent())
                true
            } catch (e: ActivityNotFoundException) {
                fileChooserCallback = null
                false
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::binding.isInitialized) binding.webView.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        // The signed-in session lives in a cookie; flush it so a process kill
        // between launches doesn't quietly sign the user out.
        CookieManager.getInstance().flush()
        if (::binding.isInitialized) binding.webView.onPause()
    }

    override fun onResume() {
        super.onResume()
        if (::binding.isInitialized) binding.webView.onResume()
    }

    override fun onDestroy() {
        if (::binding.isInitialized) binding.webView.destroy()
        super.onDestroy()
    }

    private companion object {
        /**
         * Small adjustments so the web UI reads as a native app: the PWA
         * install entry is meaningless once installed, and the server address
         * has to be reachable from somewhere. Both hook into the existing
         * account dropdown rather than adding native chrome on top.
         */
        const val NATIVE_GLUE_JS = """
            (function () {
              if (window.__dealRadarNative) return;
              window.__dealRadarNative = true;
              document.documentElement.classList.add('dr-native-android');

              var install = document.getElementById('install-item');
              if (install) install.classList.add('hidden');

              var drop = document.getElementById('user-drop');
              if (drop && window.DealRadarNative) {
                var item = document.createElement('button');
                item.className = 'drop-item';
                // Matches the icon + label shape of the page's own menu items.
                item.innerHTML = '<svg class="ico" aria-hidden="true">'
                  + '<use href="#i-sliders"></use></svg>';
                item.appendChild(document.createTextNode('App settings'));
                item.addEventListener('click', function () {
                  window.DealRadarNative.openSettings();
                });
                var signOut = drop.querySelector('.drop-item.danger');
                if (signOut) drop.insertBefore(item, signOut); else drop.appendChild(item);
              }
            })();
        """
    }
}
