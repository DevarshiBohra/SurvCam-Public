package org.example.survcam.activities

import android.content.res.Configuration
import android.os.Bundle
import android.util.Base64
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import androidx.fragment.app.Fragment
import org.example.survcam.R
import org.example.survcam.models.Camera
import org.example.survcam.storage.AppPreferences

/**
 * Displays a single "page" of up to 6 live MJPEG camera feeds in one WebView.
 * CSS grid adapts automatically to the number of feeds and the current orientation.
 */
class MultiCameraFragment : Fragment() {

    private lateinit var webView: WebView
    private lateinit var loadingBar: ProgressBar
    private var cameras: List<Camera> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        @Suppress("UNCHECKED_CAST", "DEPRECATION")
        cameras = (arguments?.getSerializable(ARG_CAMERAS) as? ArrayList<Camera>).orEmpty()
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_multi_camera, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        webView     = view.findViewById(R.id.gridWebView)
        loadingBar  = view.findViewById(R.id.loadingBar)

        // GPU-transform zoom: scale/translate the WebView as a texture — no
        // DOM re-layout, no MJPEG stream interruption → perfectly smooth.
        val zoomLayout = view.findViewById<org.example.survcam.utils.ZoomableFrameLayout>(R.id.zoomLayout)
        zoomLayout.onZoomChanged = { scale, tx, ty ->
            webView.scaleX       = scale
            webView.scaleY       = scale
            webView.translationX = tx
            webView.translationY = ty
            // Prevent ViewPager2 from stealing horizontal swipes while zoomed in
            (requireActivity() as? MultiCameraActivity)?.setSwipingEnabled(scale <= 1f)
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                loadingBar.visibility = View.GONE
            }
        }
        webView.settings.apply {
            javaScriptEnabled    = true
            cacheMode            = WebSettings.LOAD_NO_CACHE
            mixedContentMode     = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            loadWithOverviewMode = true
            useWideViewPort      = true
            // Built-in zoom disabled — zoom is handled by ZoomableFrameLayout
            // via GPU view transforms (scaleX/Y + translationX/Y) which are
            // frame-rate smooth even with 6 live MJPEG streams.
            setSupportZoom(false)
        }

        loadGrid(resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
    }

    // Called by the host activity when orientation changes (configChanges prevents recreation)
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (::webView.isInitialized) {
            loadGrid(newConfig.orientation == Configuration.ORIENTATION_LANDSCAPE)
        }
    }

    override fun onResume() {
        super.onResume()
        if (::webView.isInitialized) {
            webView.onResume()
            webView.resumeTimers()   // re-start MJPEG network requests
        }
    }

    override fun onPause() {
        super.onPause()
        if (::webView.isInitialized) {
            webView.pauseTimers()    // stop MJPEG network requests for off-screen pages
            webView.onPause()
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        webView.destroy()
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private fun loadGrid(isLandscape: Boolean) {
        val prefs     = AppPreferences(requireContext())
        val serverUrl = prefs.getServerUrl()
        val token     = prefs.getToken().orEmpty()
        loadingBar.visibility = View.VISIBLE
        webView.loadData(buildHtml(cameras, isLandscape, serverUrl, token), "text/html", "base64")
    }

    // ── Companion: pure HTML/CSS grid builder ──────────────────────────────────

    companion object {
        private const val ARG_CAMERAS = "cameras"

        fun newInstance(cameras: List<Camera>) = MultiCameraFragment().apply {
            arguments = Bundle().apply { putSerializable(ARG_CAMERAS, ArrayList(cameras)) }
        }

        /**
         * Builds a self-contained HTML page that streams up to 6 MJPEG feeds
         * laid out in the optimal CSS grid for [n] feeds and the current orientation.
         *
         * Adaptive grid strategy (portrait):
         *   1 → full screen
         *   2 → 2 stacked rows, each full width
         *   3 → top cell spans 2 cols, 2 cells below
         *   4 → 2 × 2
         *   5 → 2 × 2 above + bottom cell spans full width
         *   6 → 2 × 3
         *
         * Landscape flips to 3 columns for 3-6 feeds to better match 16:9 ratios.
         */
        fun buildHtml(
            cameras: List<Camera>,
            isLandscape: Boolean,
            serverUrl: String,
            token: String
        ): String {
            val n = cameras.size.coerceIn(1, 6)
            val (areas, cols, rows) = gridTemplate(n, isLandscape)

            val cells = cameras.take(n).mapIndexed { i, cam ->
                val letter = 'a' + i
                val url    = "$serverUrl/live/${cam.cameraId}?access_token=$token"
                val label  = (cam.name ?: cam.cameraId)
                    .replace("&", "&amp;").replace("<", "&lt;").replace("\"", "&quot;")
                """<div class="c" style="grid-area:$letter">""" +
                """<img src="$url" alt="">""" +
                """<span class="l">$label</span></div>"""
            }.joinToString("")

            val html = """
                <!doctype html><html><head>
                <meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
                <style>
                *{box-sizing:border-box;margin:0;padding:0}
                html,body{width:100%;height:100%;background:#0B0F14;overflow:hidden}
                .grid{
                  display:grid;width:100vw;height:100vh;gap:2px;background:#111;
                  grid-template-areas:$areas;
                  grid-template-columns:$cols;
                  grid-template-rows:$rows;
                }
                .c{
                  position:relative;overflow:hidden;background:#0B0F14;
                  display:flex;align-items:center;justify-content:center;
                }
                .c img{width:100%;height:100%;object-fit:contain}
                .l{
                  position:absolute;bottom:4px;left:4px;
                  color:#fff;font:bold 11px/1 sans-serif;
                  background:rgba(0,0,0,.60);padding:2px 7px;border-radius:3px;
                  pointer-events:none;white-space:nowrap;
                }
                </style></head>
                <body><div class="grid">$cells</div></body></html>
            """.trimIndent()

            return Base64.encodeToString(html.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
        }

        /**
         * Returns (grid-template-areas, grid-template-columns, grid-template-rows)
         * as CSS value strings for [n] cameras in portrait or landscape.
         */
        private fun gridTemplate(n: Int, land: Boolean): Triple<String, String, String> {
            return if (land) {
                // Landscape → wider slots suit 16:9 streams better
                when (n) {
                    1    -> Triple("'a'",                 "1fr",           "1fr")
                    2    -> Triple("'a b'",               "1fr 1fr",       "1fr")
                    3    -> Triple("'a b c'",             "1fr 1fr 1fr",   "1fr")
                    4    -> Triple("'a b' 'c d'",         "1fr 1fr",       "1fr 1fr")
                    5    -> Triple("'a b c' 'd e e'",     "1fr 1fr 1fr",   "1fr 1fr")
                    else -> Triple("'a b c' 'd e f'",     "1fr 1fr 1fr",   "1fr 1fr")
                }
            } else {
                // Portrait → 2-column layout; odd camera gets full-width row
                when (n) {
                    1    -> Triple("'a'",                 "1fr",           "1fr")
                    2    -> Triple("'a' 'b'",             "1fr",           "1fr 1fr")
                    3    -> Triple("'a b' 'c c'",         "1fr 1fr",       "1fr 1fr")
                    4    -> Triple("'a b' 'c d'",         "1fr 1fr",       "1fr 1fr")
                    5    -> Triple("'a b' 'c d' 'e e'",   "1fr 1fr",       "1fr 1fr 1fr")
                    else -> Triple("'a b' 'c d' 'e f'",   "1fr 1fr",       "1fr 1fr 1fr")
                }
            }
        }
    }
}
