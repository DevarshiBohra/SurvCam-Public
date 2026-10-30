package org.example.survcam.activities

import android.content.res.Configuration
import android.os.Bundle
import android.util.Base64
import android.view.View
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.example.survcam.R
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants

class LiveViewActivity : AppCompatActivity() {

    private lateinit var root: LinearLayout
    private lateinit var titleText: TextView
    private lateinit var liveWebView: WebView
    private lateinit var insetsController: WindowInsetsControllerCompat

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_live_view)

        root = findViewById(R.id.liveViewRoot)
        titleText = findViewById(R.id.titleText)
        liveWebView = findViewById(R.id.liveWebView)

        insetsController = WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        // Insets listener: portrait = bars + 12 dp LR safe margin; landscape = 0
        val safe = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
                v.setPadding(0, 0, 0, 0)
            } else {
                val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
                v.setPadding(bars.left + safe, bars.top, bars.right + safe, bars.bottom)
            }
            insets
        }

        val cameraId   = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        val prefs      = AppPreferences(this)
        val serverUrl  = prefs.getServerUrl()
        val token      = prefs.getToken().orEmpty()
        val liveUrl    = "$serverUrl/live/$cameraId?access_token=$token"

        titleText.text = "Live View: $cameraId"

        liveWebView.apply {
            webViewClient = WebViewClient()
            settings.javaScriptEnabled = true
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            // ── Pinch-to-zoom ───────────────────────────────────────────────
            settings.setSupportZoom(true)
            settings.builtInZoomControls  = true
            settings.displayZoomControls  = false   // hide the +/- buttons
            loadData(makeLiveViewerHtml(liveUrl), "text/html", "base64")
        }

        applyOrientation(resources.configuration.orientation)
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyOrientation(newConfig.orientation)
    }

    private fun applyOrientation(orientation: Int) {
        if (orientation == Configuration.ORIENTATION_LANDSCAPE) {
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            titleText.visibility = View.GONE
            root.setPadding(0, 0, 0, 0)
        } else {
            insetsController.show(WindowInsetsCompat.Type.systemBars())
            titleText.visibility = View.VISIBLE
            root.requestApplyInsets()   // re-triggers the insets listener above
        }
    }

    private fun makeLiveViewerHtml(liveUrl: String): String {
        // user-scalable=yes  →  WebView pinch-to-zoom is allowed
        val html = """
            <!doctype html>
            <html>
            <head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
                <style>
                    html, body {
                        margin: 0;
                        width: 100%;
                        height: 100%;
                        overflow: hidden;
                        background: #0B0F14;
                    }
                    body {
                        display: flex;
                        align-items: center;
                        justify-content: center;
                    }
                    img {
                        width: 100vw;
                        height: 100vh;
                        object-fit: contain;
                        display: block;
                        background: #0B0F14;
                    }
                </style>
            </head>
            <body>
                <img src="$liveUrl" alt="Live stream">
            </body>
            </html>
        """.trimIndent()
        return Base64.encodeToString(html.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
    }
}
