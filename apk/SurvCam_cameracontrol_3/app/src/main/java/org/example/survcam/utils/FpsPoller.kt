package org.example.survcam.utils

import android.os.Handler
import android.os.Looper
import org.example.survcam.api.ApiService
import org.example.survcam.models.FpsResponse
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

/**
 * Polls GET /streams/fps once a second and passes the result to [onUpdate].
 * The live frames per second of every camera are shown to all signed-in users.
 *
 * Call [start] from onResume and [stop] from onPause.
 */
class FpsPoller(
    private val api: ApiService,
    private val onUpdate: (Map<String, Int>) -> Unit
) {
    private val handler = Handler(Looper.getMainLooper())
    private var running = false
    private var inFlight = false

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            if (!inFlight) poll()
            handler.postDelayed(this, INTERVAL_MS)
        }
    }

    fun start() {
        if (running) return
        running = true
        handler.removeCallbacks(tick)
        handler.post(tick)
    }

    fun stop() {
        running = false
        handler.removeCallbacks(tick)
    }

    private fun poll() {
        inFlight = true
        api.streamFps().enqueue(object : Callback<FpsResponse> {
            override fun onResponse(call: Call<FpsResponse>, response: Response<FpsResponse>) {
                inFlight = false
                val fps = response.body()?.fps
                if (running && response.isSuccessful && fps != null) onUpdate(fps)
            }

            override fun onFailure(call: Call<FpsResponse>, t: Throwable) {
                inFlight = false   // try again on the next tick
            }
        })
    }

    companion object {
        private const val INTERVAL_MS = 1_000L
    }
}
