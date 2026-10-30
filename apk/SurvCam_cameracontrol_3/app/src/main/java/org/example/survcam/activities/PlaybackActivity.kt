package org.example.survcam.activities

import android.app.DownloadManager
import android.content.Context
import android.content.res.Configuration
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.View
import android.webkit.URLUtil
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.example.survcam.R
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants

class PlaybackActivity : AppCompatActivity() {

    private lateinit var root: LinearLayout
    private lateinit var titleText: TextView
    private lateinit var playbackWebView: WebView
    private lateinit var insetsController: WindowInsetsControllerCompat

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_playback)

        root = findViewById(R.id.playbackRoot)
        titleText = findViewById(R.id.titleText)
        playbackWebView = findViewById(R.id.playbackWebView)

        insetsController = WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

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
        val playbackUrl = "$serverUrl/playback/$cameraId?access_token=$token"

        titleText.text = "Recordings: $cameraId"

        playbackWebView.apply {
            webViewClient = WebViewClient()
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            // ── Pinch-to-zoom for the server-rendered player page ───────────
            settings.setSupportZoom(true)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
                val authedUrl = ensureTokenInUrl(url, token)
                downloadRecording(authedUrl, userAgent, contentDisposition, mimeType)
            }
            loadUrl(playbackUrl)
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
            root.requestApplyInsets()
        }
    }

    private fun ensureTokenInUrl(url: String, token: String): String {
        if (token.isBlank() || url.contains("access_token=")) return url
        val sep = if (url.contains("?")) "&" else "?"
        return "$url${sep}access_token=$token"
    }

    private fun downloadRecording(
        url: String, userAgent: String,
        contentDisposition: String, mimeType: String
    ) {
        val filename = URLUtil.guessFileName(url, contentDisposition, mimeType)
        val request = DownloadManager.Request(Uri.parse(url))
            .setTitle(filename)
            .setDescription("Downloading recording")
            .setMimeType(mimeType)
            .addRequestHeader("User-Agent", userAgent)
            .setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            )
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
        (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
        Toast.makeText(this, "Download started", Toast.LENGTH_SHORT).show()
    }
}
