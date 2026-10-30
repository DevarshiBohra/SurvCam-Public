package org.example.survcam.activities

import android.app.DownloadManager
import android.content.Context
import android.content.res.Configuration
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import org.example.survcam.R
import org.example.survcam.api.ApiClient
import org.example.survcam.models.RecordingManifestResponse
import org.example.survcam.models.RecordingSegment
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.DoubleTapListener
import org.example.survcam.utils.NetworkUtils
import org.example.survcam.utils.ZoomableFrameLayout
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Plays one clock-hour of recordings as a single continuous video by
 * queueing all of that hour's 5-minute segments into one ExoPlayer playlist.
 *
 * New in this version:
 *  • Landscape → true fullscreen (system bars hidden, controls auto-hide).
 *    Tap the video to reveal controls for 4 s; they then disappear again.
 *  • Pinch-to-zoom on the video (1×–4×) via ZoomableFrameLayout.
 *    Double-tap seek zones still work as before (single-finger only).
 */
@OptIn(UnstableApi::class)
class NativePlaybackActivity : AppCompatActivity() {

    // ── View references ────────────────────────────────────────────────────
    private lateinit var rootLayout:         LinearLayout
    private lateinit var playerContainer:    ZoomableFrameLayout
    private lateinit var playerView:         PlayerView
    private lateinit var controlsPanel:      LinearLayout
    private lateinit var tapToRevealOverlay: View
    private lateinit var seekBar:            SeekBar
    private lateinit var currentTimeText:    TextView
    private lateinit var statusText:         TextView
    private lateinit var playPauseButton:    Button
    private lateinit var speedButton:        Button
    private lateinit var downloadButton:     Button
    private lateinit var seekFeedbackText:   TextView
    private lateinit var bufferingOverlay:   View
    private lateinit var bufferingProgressBar: ProgressBar
    private lateinit var bufferingText:      TextView

    // ── Playback state ─────────────────────────────────────────────────────
    private lateinit var cameraId:   String
    private lateinit var date:       String
    private var hour:                Int = 0
    private lateinit var preferences: AppPreferences

    private var player:              ExoPlayer? = null
    private var segments:            List<RecordingSegment> = emptyList()
    private var segmentOffsetsMs:    List<Long>  = emptyList()
    private var totalDurationMs:     Long = 0
    private var hourStartMs:         Long = 0

    private val speedOptions = listOf(1.0f, 1.25f, 1.5f, 2.0f, 4.0f, 8.0f)
    private var speedIndex   = 0

    private val progressHandler  = Handler(Looper.getMainLooper())
    private var isUserSeeking    = false
    private val progressRunnable = object : Runnable {
        override fun run() {
            updateProgressUi()
            progressHandler.postDelayed(this, 500)
        }
    }

    // ── Landscape controls auto-hide ───────────────────────────────────────
    private lateinit var insetsController: WindowInsetsControllerCompat
    private val controlsHandler      = Handler(Looper.getMainLooper())
    private val hideControlsRunnable = Runnable { hideControlsInLandscape() }
    private val CONTROLS_TIMEOUT_MS  = 4_000L

    // ════════════════════════════════════════════════════════════════════════
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_native_playback)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        date        = intent.getStringExtra(Constants.EXTRA_DATE).orEmpty()
        hour        = intent.getIntExtra(Constants.EXTRA_HOUR, 0)
        preferences = AppPreferences(this)

        // ── Bind views ──────────────────────────────────────────────────────
        rootLayout            = findViewById(R.id.nativePlaybackRoot)
        playerContainer       = findViewById(R.id.playerContainer)
        playerView            = findViewById(R.id.playerView)
        controlsPanel         = findViewById(R.id.controlsPanel)
        tapToRevealOverlay    = findViewById(R.id.tapToRevealOverlay)
        seekBar               = findViewById(R.id.seekBar)
        currentTimeText       = findViewById(R.id.currentTimeText)
        statusText            = findViewById(R.id.statusText)
        playPauseButton       = findViewById(R.id.playPauseButton)
        speedButton           = findViewById(R.id.speedButton)
        downloadButton        = findViewById(R.id.downloadButton)
        seekFeedbackText      = findViewById(R.id.seekFeedbackText)
        bufferingOverlay      = findViewById(R.id.bufferingOverlay)
        bufferingProgressBar  = findViewById(R.id.bufferingProgressBar)
        bufferingText         = findViewById(R.id.bufferingText)

        insetsController = WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        // ── Safe-margin insets (portrait) / fullscreen (landscape) ──────────
        val safe = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(rootLayout) { v, insets ->
            if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
                v.setPadding(0, 0, 0, 0)
            } else {
                val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
                v.setPadding(bars.left + safe, bars.top, bars.right + safe, bars.bottom)
            }
            insets
        }

        // ── Pinch-to-zoom on the player ─────────────────────────────────────
        playerContainer.onZoomChanged = { scale, tx, ty ->
            playerView.scaleX       = scale
            playerView.scaleY       = scale
            playerView.translationX = tx
            playerView.translationY = ty
        }

        // ── Tap-to-reveal controls in landscape ─────────────────────────────
        tapToRevealOverlay.setOnClickListener { showControlsInLandscape() }

        setupTapZones()
        setupControls()
        loadManifest()

        applyOrientation(resources.configuration.orientation)
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyOrientation(newConfig.orientation)
    }

    // ── Orientation handling ───────────────────────────────────────────────

    private fun applyOrientation(orientation: Int) {
        if (orientation == Configuration.ORIENTATION_LANDSCAPE) {
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            rootLayout.setPadding(0, 0, 0, 0)
            // Start with controls visible for 4 s, then auto-hide
            controlsPanel.visibility      = View.VISIBLE
            tapToRevealOverlay.visibility = View.GONE
            scheduleHideControls()
        } else {
            controlsHandler.removeCallbacks(hideControlsRunnable)
            insetsController.show(WindowInsetsCompat.Type.systemBars())
            rootLayout.requestApplyInsets()
            controlsPanel.visibility      = View.VISIBLE
            tapToRevealOverlay.visibility = View.GONE
        }
    }

    /** Show controls and (re)start the auto-hide timer — landscape only. */
    private fun showControlsInLandscape() {
        tapToRevealOverlay.visibility = View.GONE
        controlsPanel.visibility      = View.VISIBLE
        scheduleHideControls()
    }

    private fun scheduleHideControls() {
        controlsHandler.removeCallbacks(hideControlsRunnable)
        controlsHandler.postDelayed(hideControlsRunnable, CONTROLS_TIMEOUT_MS)
    }

    private fun hideControlsInLandscape() {
        if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
            controlsPanel.visibility      = View.GONE
            tapToRevealOverlay.visibility = View.VISIBLE
        }
    }

    // ── Existing playback logic (unchanged) ────────────────────────────────

    private fun setupTapZones() {
        findViewById<View>(R.id.leftTapZone).setOnTouchListener(
            DoubleTapListener(this) {
                seekRelative(-10_000)
                showSeekFeedback("-10s")
            }
        )
        findViewById<View>(R.id.rightTapZone).setOnTouchListener(
            DoubleTapListener(this) {
                seekRelative(10_000)
                showSeekFeedback("+10s")
            }
        )
    }

    private fun setupControls() {
        playPauseButton.setOnClickListener {
            val exo = player ?: return@setOnClickListener
            if (exo.isPlaying) exo.pause() else exo.play()
            if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
                scheduleHideControls()
            }
        }

        speedButton.setOnClickListener {
            speedIndex = (speedIndex + 1) % speedOptions.size
            val speed = speedOptions[speedIndex]
            player?.setPlaybackSpeed(speed)
            speedButton.text = formatSpeed(speed)
            if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
                scheduleHideControls()
            }
        }

        downloadButton.setOnClickListener { downloadCurrentClip() }

        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, progress: Int, fromUser: Boolean) {
                if (fromUser) currentTimeText.text = formatClockTime(hourStartMs + progress)
            }
            override fun onStartTrackingTouch(bar: SeekBar) { isUserSeeking = true }
            override fun onStopTrackingTouch(bar: SeekBar) {
                isUserSeeking = false
                seekToGlobalMs(bar.progress.toLong())
                if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) {
                    scheduleHideControls()
                }
            }
        })
    }

    private fun loadManifest() {
        statusText.text = "Loading recordings..."
        ApiClient.create(this, preferences.getServerUrl())
            .recordingManifest(cameraId, date, hour)
            .enqueue(object : Callback<RecordingManifestResponse> {
                override fun onResponse(
                    call: Call<RecordingManifestResponse>,
                    response: Response<RecordingManifestResponse>
                ) {
                    if (response.isSuccessful) {
                        val body = response.body()
                        if (body == null || body.segments.isEmpty()) {
                            statusText.text = "No recordings found for this hour."
                            return
                        }
                        segments = body.segments
                        preparePlayer()
                        statusText.text = ""
                    } else {
                        statusText.text = NetworkUtils.readableError(response)
                    }
                }
                override fun onFailure(call: Call<RecordingManifestResponse>, t: Throwable) {
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }

    private fun preparePlayer() {
        val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.getDefault())
        hourStartMs = try {
            isoFormat.parse(segments.first().startTime)?.time ?: 0L
        } catch (e: Exception) { 0L }

        val offsets = mutableListOf<Long>()
        var cumulative = 0L
        for (segment in segments) {
            offsets.add(cumulative)
            cumulative += (segment.durationSeconds * 1000).toLong()
        }
        segmentOffsetsMs  = offsets
        totalDurationMs   = cumulative
        seekBar.max       = totalDurationMs.toInt().coerceAtLeast(1)

        val serverUrl = preferences.getServerUrl()
        val token     = preferences.getToken().orEmpty()

        val exo = ExoPlayer.Builder(this).build()
        player  = exo
        playerView.player = exo

        bufferingOverlay.visibility = View.VISIBLE

        exo.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playPauseButton.text = if (isPlaying) "Pause" else "Play"
            }
            override fun onPlaybackStateChanged(state: Int) {
                when (state) {
                    Player.STATE_BUFFERING -> bufferingOverlay.visibility = View.VISIBLE
                    Player.STATE_READY, Player.STATE_ENDED ->
                        bufferingOverlay.visibility = View.GONE
                    else -> {}
                }
            }
        })

        val mediaItems = segments.map { segment ->
            MediaItem.fromUri("$serverUrl${segment.downloadUrl}?access_token=$token")
        }
        exo.setMediaItems(mediaItems)
        exo.prepare()
        exo.playWhenReady = true

        progressHandler.post(progressRunnable)
    }

    private fun updateProgressUi() {
        val exo = player ?: return
        val bufferedPct = exo.bufferedPercentage.coerceIn(0, 100)
        bufferingProgressBar.progress = bufferedPct
        bufferingText.text = "Buffering... $bufferedPct%"
        if (isUserSeeking) return
        val windowIndex = exo.currentMediaItemIndex
        if (windowIndex >= segmentOffsetsMs.size) return
        val globalMs = segmentOffsetsMs[windowIndex] + exo.currentPosition
        seekBar.progress = globalMs.toInt().coerceIn(0, seekBar.max)
        currentTimeText.text = formatClockTime(hourStartMs + globalMs)
    }

    private fun seekRelative(deltaMs: Long) {
        val exo        = player ?: return
        val windowIndex = exo.currentMediaItemIndex
        if (windowIndex >= segmentOffsetsMs.size) return
        val currentGlobalMs = segmentOffsetsMs[windowIndex] + exo.currentPosition
        val targetMs        = (currentGlobalMs + deltaMs).coerceIn(0, totalDurationMs)
        seekToGlobalMs(targetMs)
    }

    private fun seekToGlobalMs(globalMs: Long) {
        val clamped = globalMs.coerceIn(0, totalDurationMs)
        var targetWindow = 0
        for (i in segmentOffsetsMs.indices) {
            if (clamped >= segmentOffsetsMs[i]) targetWindow = i
        }
        val positionInWindow = clamped - segmentOffsetsMs[targetWindow]
        player?.seekTo(targetWindow, positionInWindow)
        seekBar.progress = clamped.toInt()
        currentTimeText.text = formatClockTime(hourStartMs + clamped)
    }

    private fun downloadCurrentClip() {
        val exo         = player ?: return
        val windowIndex = exo.currentMediaItemIndex
        if (windowIndex >= segments.size) return
        val segment   = segments[windowIndex]
        val serverUrl = preferences.getServerUrl()
        val token     = preferences.getToken().orEmpty()
        val url       = "$serverUrl${segment.downloadUrl}?access_token=$token"
        val request   = DownloadManager.Request(Uri.parse(url))
            .setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            )
            .setDestinationInExternalPublicDir(
                Environment.DIRECTORY_DOWNLOADS, segment.filename
            )
        (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
        Toast.makeText(this, "Downloading ${segment.filename}", Toast.LENGTH_SHORT).show()
    }

    private fun showSeekFeedback(text: String) {
        seekFeedbackText.text = text
        seekFeedbackText.visibility = View.VISIBLE
        seekFeedbackText.postDelayed({ seekFeedbackText.visibility = View.GONE }, 500)
    }

    private fun formatSpeed(speed: Float) =
        if (speed == speed.toLong().toFloat()) "${speed.toLong()}x" else "${speed}x"

    private fun formatClockTime(epochMs: Long) =
        SimpleDateFormat("h:mm:ss a", Locale.getDefault()).format(Date(epochMs))

    // ── Lifecycle ──────────────────────────────────────────────────────────
    override fun onPause() {
        super.onPause()
        player?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        progressHandler.removeCallbacksAndMessages(null)
        controlsHandler.removeCallbacksAndMessages(null)
        player?.release()
        player = null
    }
}
