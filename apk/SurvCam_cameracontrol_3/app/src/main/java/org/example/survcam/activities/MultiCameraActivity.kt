package org.example.survcam.activities

import android.content.res.Configuration
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.viewpager2.widget.ViewPager2
import org.example.survcam.R
import org.example.survcam.adapter.MultiCameraPageAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.Camera
import org.example.survcam.models.CameraResponse
import org.example.survcam.storage.AppPreferences
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

/**
 * Shows up to [CAMERAS_PER_PAGE] live MJPEG feeds per page with swipe-to-navigate.
 * Only online cameras (status == "online") are shown; the adaptive grid resizes
 * based on how many cameras fit on a page.
 *
 * The camera list is refreshed automatically every [REFRESH_MS] milliseconds.
 * If the set of online cameras changes, the adapter is rebuilt — fragments whose
 * page content is unchanged are reused without restarting their streams.
 */
class MultiCameraActivity : AppCompatActivity() {

    // ── Views ──────────────────────────────────────────────────────────────────
    private lateinit var viewPager:      ViewPager2
    private lateinit var topBar:         LinearLayout
    private lateinit var titleText:      TextView
    private lateinit var emptyText:      TextView
    private lateinit var dotsContainer:  LinearLayout

    // ── State ──────────────────────────────────────────────────────────────────
    private lateinit var preferences:       AppPreferences
    private lateinit var insetsController:  WindowInsetsControllerCompat
    private var currentPages: List<List<Camera>> = emptyList()
    private var isInitialLoad = true

    // ── Auto-refresh ───────────────────────────────────────────────────────────
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            fetchCameras()
            refreshHandler.postDelayed(this, REFRESH_MS)
        }
    }

    companion object {
        const val CAMERAS_PER_PAGE = 6
        const val REFRESH_MS       = 30_000L
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_multi_camera)

        preferences = AppPreferences(this)
        insetsController = WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        viewPager     = findViewById(R.id.viewPager)
        topBar        = findViewById(R.id.topBar)
        titleText     = findViewById(R.id.titleText)
        emptyText     = findViewById(R.id.emptyText)
        dotsContainer = findViewById(R.id.dotsContainer)

        // Push topBar content below the status bar; keep left/right safe margin
        val safeLR = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(topBar) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left + safeLR, bars.top, bars.right + safeLR, 0)
            insets
        }

        // Keep dots above the navigation bar
        ViewCompat.setOnApplyWindowInsetsListener(dotsContainer) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val lp   = v.layoutParams as FrameLayout.LayoutParams
            lp.bottomMargin = bars.bottom + (16 * resources.displayMetrics.density).toInt()
            v.layoutParams = lp
            insets
        }

        viewPager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) = updateDots(position)
        })

        applyOrientation(resources.configuration.orientation)
        fetchCameras()   // immediate first load
    }

    override fun onResume() {
        super.onResume()
        // Schedule the 30-second auto-refresh cycle
        refreshHandler.postDelayed(refreshRunnable, REFRESH_MS)
    }

    override fun onPause() {
        super.onPause()
        refreshHandler.removeCallbacks(refreshRunnable)
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyOrientation(newConfig.orientation)
        // Fragments observe onConfigurationChanged themselves and reload their WebViews.
    }

    /** Called by the active fragment to disable page swiping while zoomed in. */
    fun setSwipingEnabled(enabled: Boolean) {
        if (viewPager.isUserInputEnabled != enabled) viewPager.isUserInputEnabled = enabled
    }

    // ── Orientation ────────────────────────────────────────────────────────────

    private fun applyOrientation(orientation: Int) {
        if (orientation == Configuration.ORIENTATION_LANDSCAPE) {
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            topBar.visibility = View.GONE
        } else {
            insetsController.show(WindowInsetsCompat.Type.systemBars())
            topBar.visibility = View.VISIBLE
        }
    }

    // ── Data fetching & refresh ────────────────────────────────────────────────

    private fun fetchCameras() {
        ApiClient.create(this, preferences.getServerUrl())
            .cameras()
            .enqueue(object : Callback<CameraResponse> {
                override fun onResponse(
                    call: Call<CameraResponse>,
                    response: Response<CameraResponse>
                ) {
                    if (!response.isSuccessful) {
                        if (isInitialLoad) showEmpty("Server returned an error")
                        return
                    }
                    val online = response.body()?.cameras.orEmpty()
                        .filter { it.status.equals("online", ignoreCase = true) }
                    applyOnlineCameras(online)
                }

                override fun onFailure(call: Call<CameraResponse>, t: Throwable) {
                    if (isInitialLoad) showEmpty("Could not reach server")
                }
            })
    }

    private fun applyOnlineCameras(online: List<Camera>) {
        val newPages = online.chunked(CAMERAS_PER_PAGE)

        // Skip the rebuild if nothing actually changed (avoids stream interruptions)
        if (!isInitialLoad && pagesEqual(currentPages, newPages)) return

        currentPages  = newPages
        isInitialLoad = false

        if (newPages.isEmpty()) {
            showEmpty("No cameras are currently online")
            return
        }

        // Hide error label, make pager visible
        emptyText.visibility  = View.GONE
        viewPager.visibility  = View.VISIBLE

        val savedPage = viewPager.currentItem
        viewPager.adapter = MultiCameraPageAdapter(this, newPages)
        viewPager.setCurrentItem(savedPage.coerceAtMost(newPages.size - 1), false)

        val pageLabel = if (newPages.size > 1) " · ${newPages.size} pages" else ""
        titleText.text = "Live Multi-View · ${online.size} online$pageLabel"

        buildDots(newPages.size)
        updateDots(viewPager.currentItem)
    }

    private fun showEmpty(message: String) {
        emptyText.text       = message
        emptyText.visibility = View.VISIBLE
        viewPager.visibility = View.GONE
        dotsContainer.visibility = View.GONE
        titleText.text = "Live Multi-View"
    }

    // ── Page-indicator dots ────────────────────────────────────────────────────

    private fun buildDots(pageCount: Int) {
        dotsContainer.removeAllViews()
        if (pageCount <= 1) {
            dotsContainer.visibility = View.GONE
            return
        }
        dotsContainer.visibility = View.VISIBLE
        val dp6 = (6 * resources.displayMetrics.density).toInt()
        val dp4 = (4 * resources.displayMetrics.density).toInt()
        repeat(pageCount) {
            val dot = View(this).apply {
                layoutParams = LinearLayout.LayoutParams(dp6, dp6).also { lp ->
                    lp.setMargins(dp4, 0, dp4, 0)
                }
                setBackgroundResource(R.drawable.status_online)   // white circle
                alpha = 0.35f
            }
            dotsContainer.addView(dot)
        }
    }

    private fun updateDots(active: Int) {
        for (i in 0 until dotsContainer.childCount) {
            dotsContainer.getChildAt(i)?.alpha = if (i == active) 1.0f else 0.35f
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /** Returns true only if both page lists have the same cameras in the same order. */
    private fun pagesEqual(a: List<List<Camera>>, b: List<List<Camera>>): Boolean {
        if (a.size != b.size) return false
        return a.zip(b).all { (pa, pb) ->
            pa.size == pb.size &&
            pa.zip(pb).all { (ca, cb) -> ca.cameraId == cb.cameraId }
        }
    }
}
