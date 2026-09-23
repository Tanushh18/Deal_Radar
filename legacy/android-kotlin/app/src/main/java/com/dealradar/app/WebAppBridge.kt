package com.dealradar.app

import android.webkit.JavascriptInterface

/**
 * The only surface the web UI can reach into the app through.
 *
 * Deliberately tiny: a JavascriptInterface is callable by any script the
 * loaded page runs, so nothing here may touch the filesystem, credentials, or
 * arbitrary intents — opening the app's own settings screen and suspending
 * pull-to-refresh is the whole API.
 */
class WebAppBridge(
    private val onOpenSettings: () -> Unit,
    private val onPullToRefreshChanged: (Boolean) -> Unit,
) {

    @JavascriptInterface
    fun openSettings() {
        onOpenSettings()
    }

    /**
     * The page owns a few full-width bottom sheets (filters, account, deal
     * detail). Those sit at scroll position 0, which is exactly the condition
     * that arms pull-to-refresh — so dragging their content down would reload
     * the app instead of scrolling the sheet. The page turns the gesture off
     * while a sheet is open and back on when it closes.
     */
    @JavascriptInterface
    fun setPullToRefresh(enabled: Boolean) {
        onPullToRefreshChanged(enabled)
    }
}
