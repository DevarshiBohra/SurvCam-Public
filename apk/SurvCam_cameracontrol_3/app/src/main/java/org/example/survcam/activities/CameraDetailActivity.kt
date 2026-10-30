package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import org.example.survcam.R
import org.example.survcam.api.ApiClient
import org.example.survcam.models.Camera
import org.example.survcam.models.CameraEventsResponse
import org.example.survcam.models.CapabilitiesResponse
import org.example.survcam.models.CommandResponse
import org.example.survcam.models.FirmwareEntry
import org.example.survcam.models.FirmwareListResponse
import org.example.survcam.models.OtaRequest
import org.example.survcam.models.RssiResponse
import org.example.survcam.models.TelemetryLatestResponse
import org.example.survcam.models.TelemetryResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.NetworkUtils
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response
import java.util.Locale

class CameraDetailActivity : BaseActivity() {

    private lateinit var cameraId:    String
    private lateinit var preferences: AppPreferences
    private lateinit var nameText:    TextView
    private lateinit var detailsText: TextView

    private lateinit var sections: List<LiveSection>

    companion object {
        private const val LIVE_INTERVAL_MS   = 5_000L    // RSSI / temperature / diagnostics
        private const val EVENTS_INTERVAL_MS = 30_000L   // reboot & dropout history
        private const val EVENTS_HOURS       = 168       // 7 days
    }

    /**
     * One toggle button + collapsible card + refresh loop.
     * Tap the button: card appears and refreshes every [intervalMs]. Tap again: hides and stops.
     */
    private inner class LiveSection(
        private val button: Button,
        private val card: TextView,
        private val label: String,
        private val loadingText: String,
        private val intervalMs: Long,
        private val loader: (TextView) -> Unit
    ) {
        private val handler = Handler(Looper.getMainLooper())
        private val runnable = object : Runnable {
            override fun run() {
                loader(card)
                handler.postDelayed(this, intervalMs)
            }
        }

        init {
            button.setOnClickListener {
                if (card.visibility == View.VISIBLE) {
                    card.visibility = View.GONE
                    button.text = label
                    handler.removeCallbacks(runnable)
                } else {
                    card.visibility = View.VISIBLE
                    card.text = loadingText
                    button.text = "$label  ●  Live"
                    handler.removeCallbacks(runnable)
                    handler.post(runnable)
                }
            }
        }

        fun pause() = handler.removeCallbacks(runnable)

        /** Opens the card if it is closed (used to show update progress). */
        fun open() {
            if (card.visibility != View.VISIBLE) button.performClick()
        }

        fun resume() {
            if (card.visibility == View.VISIBLE) {
                handler.removeCallbacks(runnable)
                handler.post(runnable)
            }
        }
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_camera_detail)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        preferences = AppPreferences(this)
        nameText    = findViewById(R.id.nameText)
        detailsText = findViewById(R.id.detailsText)

        nameText.text = intent.getStringExtra(Constants.EXTRA_CAMERA_NAME) ?: cameraId

        findViewById<Button>(R.id.liveButton).setOnClickListener {
            startActivity(
                Intent(this, LiveViewActivity::class.java)
                    .putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
            )
        }
        findViewById<Button>(R.id.playbackButton).setOnClickListener {
            startActivity(
                Intent(this, RecordingDaysActivity::class.java)
                    .putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
            )
        }
        findViewById<Button>(R.id.snapshotsButton).setOnClickListener {
            startActivity(
                Intent(this, SnapshotDaysActivity::class.java)
                    .putExtra(Constants.EXTRA_CAMERA_ID,   cameraId)
                    .putExtra(Constants.EXTRA_CAMERA_NAME, nameText.text.toString())
            )
        }
        findViewById<Button>(R.id.refreshButton).setOnClickListener { loadCamera() }

        sections = listOf(
            LiveSection(
                findViewById(R.id.rssiButton), findViewById(R.id.rssiCard),
                "RSSI", "Loading RSSI history…", LIVE_INTERVAL_MS, this::loadRssi
            ),
            LiveSection(
                findViewById(R.id.tempButton), findViewById(R.id.tempCard),
                "Temperature", "Loading temperature…", LIVE_INTERVAL_MS, this::loadTemperature
            ),
            LiveSection(
                findViewById(R.id.diagButton), findViewById(R.id.diagCard),
                "Diagnostics", "Loading diagnostics…", LIVE_INTERVAL_MS, this::loadDiagnostics
            ),
            LiveSection(
                findViewById(R.id.eventsButton), findViewById(R.id.eventsCard),
                "Reboots & Dropouts", "Loading history…", EVENTS_INTERVAL_MS, this::loadEvents
            )
        )

        // Camera Control is only for accounts the server allows (not decided by the app).
        val controlButton = findViewById<Button>(R.id.controlButton)
        controlButton.setOnClickListener { showControlMenu() }
        api().capabilities().enqueue(object : Callback<CapabilitiesResponse> {
            override fun onResponse(call: Call<CapabilitiesResponse>, response: Response<CapabilitiesResponse>) {
                if (response.isSuccessful && response.body()?.camera_control == true) {
                    controlButton.visibility = View.VISIBLE
                }
            }
            override fun onFailure(call: Call<CapabilitiesResponse>, t: Throwable) { /* stays hidden */ }
        })

        loadCamera()
    }

    override fun onPause() {
        super.onPause()
        sections.forEach { it.pause() }
    }

    override fun onResume() {
        super.onResume()
        // Resume refreshing any card that was already open
        if (::sections.isInitialized) sections.forEach { it.resume() }
    }

    // ── Camera status ──────────────────────────────────────────────────────────

    private fun loadCamera() {
        detailsText.text = "Loading camera status…"
        ApiClient.create(this, preferences.getServerUrl())
            .camera(cameraId)
            .enqueue(object : Callback<Camera> {
                override fun onResponse(call: Call<Camera>, response: Response<Camera>) {
                    if (response.isSuccessful) {
                        val cam = response.body()
                        if (cam == null) {
                            detailsText.text = "Camera details were empty."
                        } else {
                            nameText.text    = cam.name ?: cam.cameraId
                            detailsText.text = NetworkUtils.formatCameraDetails(
                                cam.cameraId, cam.status, cam.ipAddress, cam.lastSeen
                            )
                        }
                    } else {
                        detailsText.text = NetworkUtils.readableError(response)
                    }
                }
                override fun onFailure(call: Call<Camera>, t: Throwable) {
                    detailsText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }

    // ── Generic fetch-and-render helper ────────────────────────────────────────

    /**
     * Runs [call], and puts the text returned by [render] into [card].
     * [render] returns null when there is nothing to show -> [emptyText] is displayed.
     */
    private fun <T> fetch(
        card: TextView,
        what: String,
        emptyText: String,
        call: Call<T>,
        render: (T) -> String?
    ) {
        call.enqueue(object : Callback<T> {
            override fun onResponse(call: Call<T>, response: Response<T>) {
                if (!response.isSuccessful) {
                    card.text = "Error ${response.code()}: could not fetch $what"
                    return
                }
                val body = response.body()
                card.text = if (body == null) emptyText else (render(body) ?: emptyText)
            }

            override fun onFailure(call: Call<T>, t: Throwable) {
                card.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
            }
        })
    }

    private fun api() = ApiClient.create(this, preferences.getServerUrl())

    // ── RSSI (unchanged behaviour) ─────────────────────────────────────────────

    private fun loadRssi(card: TextView) {
        fetch(card, "RSSI data", "No RSSI readings found for this camera.", api().rssiHistory(cameraId)) { resp: RssiResponse ->
            if (resp.readings.isEmpty()) return@fetch null
            val sb = StringBuilder()
            sb.appendLine("%-10s  %-9s  %s".format("Time", "RSSI", "Signal"))
            sb.appendLine("─".repeat(34))
            for (r in resp.readings) {
                val rssiVal = r.rssi
                val rssiStr = if (rssiVal != null) "$rssiVal dBm" else "—"
                val quality = if (rssiVal != null) rssiQuality(rssiVal) else "—"
                sb.appendLine("%-10s  %-9s  %s".format(extractTime(r.recordedAt), rssiStr, quality))
            }
            sb.toString().trimEnd()
        }
    }

    // ── Temperature ────────────────────────────────────────────────────────────

    private fun loadTemperature(card: TextView) {
        fetch(card, "temperature data", "No readings found for this camera.", api().telemetryHistory(cameraId)) { resp: TelemetryResponse ->
            if (resp.readings.isEmpty()) return@fetch null

            val temps = resp.readings.mapNotNull { it.tempC }
            if (temps.isEmpty()) {
                return@fetch "The camera is not reporting a temperature.\n(Needs firmware that sends temp_c.)"
            }

            val sb = StringBuilder()
            // The original ESP32 chip often has no working sensor and reports a fixed 53.3 °C.
            if (temps.all { it in 53.2..53.4 }) {
                sb.appendLine("⚠ Stuck at 53.3 °C - this chip has no working")
                sb.appendLine("  temperature sensor, so these are not real.")
                sb.appendLine()
            }
            sb.appendLine("%-10s  %-9s  %s".format("Time", "Temp", "Level"))
            sb.appendLine("─".repeat(34))
            for (r in resp.readings) {
                val t = r.tempC
                val tStr = if (t != null) fmt1(t) + " °C" else "—"
                val level = if (t != null) tempLevel(t) else "—"
                sb.appendLine("%-10s  %-9s  %s".format(extractTime(r.recordedAt), tStr, level))
            }
            sb.appendLine("─".repeat(34))
            sb.append("Min ${fmt1(temps.min())}  Max ${fmt1(temps.max())}  Avg ${fmt1(temps.average())} °C")
            sb.toString()
        }
    }

    // ── Diagnostics (latest heartbeat) ─────────────────────────────────────────

    private fun loadDiagnostics(card: TextView) {
        fetch(card, "diagnostics", "No heartbeat data yet for this camera.", api().telemetryLatest(cameraId)) { resp: TelemetryLatestResponse ->
            val r = resp.latest ?: return@fetch null

            val dropNow  = discName(r.discReason)
            val dropPrev = discName(r.prevDiscReason)

            val rows = listOf(
                "Updated"       to extractTime(r.recordedAt),
                "Temperature"   to (r.tempC?.let { fmt1(it) + " °C" } ?: "—"),
                "Signal"        to (r.rssi?.let { "$it dBm (${rssiQuality(it)})" } ?: "—"),
                "WiFi channel"  to (r.channel?.toString() ?: "—"),
                "Access point"  to (r.bssid?.takeIf { it.isNotBlank() } ?: "—"),
                "Free memory"   to (r.freeHeap?.let { "${it / 1024} KB" } ?: "—"),
                "Uptime"        to (r.uptimeMs?.let { formatDuration(it / 1000) } ?: "—"),
                "Firmware"      to (r.fwVersion ?: "—"),
                "Firmware slot" to (r.otaPart ?: "—"),
                "Update"        to otaLabel(r.otaState, r.otaProgress, r.otaMsg),
                "Last reboot"   to rebootLabel(r.restartCause, r.resetReason),
                "WiFi drops"    to (r.discCount?.let { "$it since boot" } ?: "—"),
                "Last drop"     to dropNow,
                "Drop pre-boot" to dropPrev
            )
            rows.joinToString("\n") { "%-13s %s".format(it.first, it.second) }
        }
    }

    // ── Reboots & dropouts ─────────────────────────────────────────────────────

    private fun loadEvents(card: TextView) {
        fetch(card, "history", "No data.", api().cameraEvents(cameraId, EVENTS_HOURS)) { resp: CameraEventsResponse ->
            val days = resp.hours / 24
            if (resp.events.isEmpty()) {
                return@fetch "No reboots or dropouts in the last $days days."
            }

            val reboots  = resp.events.count { it.type == "reboot" }
            val dropouts = resp.events.size - reboots

            val sb = StringBuilder()
            sb.appendLine("Last $days days: $reboots reboots, $dropouts dropouts")
            sb.appendLine("─".repeat(34))
            for (e in resp.events) {
                val whenText = if (e.at.length >= 16) e.at.substring(5, 16) else e.at
                val what = if (e.type == "reboot") rebootLabel(e.restartCause, e.resetReason) else "Dropout"
                val dur  = e.durationS?.let { formatDuration(it) } ?: "—"
                sb.appendLine("%-11s %-10s %s".format(whenText, what, dur))
                if (e.type == "reboot" && (e.prevDiscReason ?: 0) != 0) {
                    sb.appendLine("   ↳ before: ${discName(e.prevDiscReason)}")
                }
            }
            sb.append("Same time on several cameras = server, broker or AP.")
            sb.toString()
        }
    }

    // ── Camera control (admin): update firmware, restart, roll back ────────────

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    private fun confirm(title: String, message: String, positive: String, action: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message)
            .setPositiveButton(positive) { _, _ -> action() }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showControlMenu() {
        val items = arrayOf("Update firmware", "Restart camera", "Roll back firmware")
        AlertDialog.Builder(this)
            .setTitle("Camera control")
            .setItems(items) { _, which ->
                when (which) {
                    0 -> prepareUpdate()
                    1 -> confirm(
                        "Restart camera?",
                        "The camera restarts and is offline for about a minute.",
                        "Restart"
                    ) { sendCommand(api().rebootCamera(cameraId), "Restart command sent") }
                    2 -> confirm(
                        "Roll back firmware?",
                        "The camera switches back to the firmware it ran before the last update, " +
                            "and restarts. This only works if it has been updated over the air before.",
                        "Roll back"
                    ) { sendCommand(api().rollbackCamera(cameraId), "Roll back command sent") }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /** Looks up what the camera runs now and the newest uploaded firmware, then asks to confirm. */
    private fun prepareUpdate() {
        toast("Checking firmware…")
        api().telemetryLatest(cameraId).enqueue(object : Callback<TelemetryLatestResponse> {
            override fun onResponse(call: Call<TelemetryLatestResponse>, response: Response<TelemetryLatestResponse>) {
                val current = response.body()?.latest?.fwVersion ?: "unknown"
                loadLatestFirmware(current)
            }
            override fun onFailure(call: Call<TelemetryLatestResponse>, t: Throwable) {
                loadLatestFirmware("unknown")
            }
        })
    }

    private fun loadLatestFirmware(currentVersion: String) {
        api().firmwareList(cameraId).enqueue(object : Callback<FirmwareListResponse> {
            override fun onResponse(call: Call<FirmwareListResponse>, response: Response<FirmwareListResponse>) {
                if (!response.isSuccessful) {
                    toast(NetworkUtils.readableError(response))
                    return
                }
                val list = response.body()
                val versions = list?.versions.orEmpty()
                if (versions.isEmpty()) {
                    toast("No firmware has been uploaded to the server yet.")
                    return
                }
                showVersionPicker(currentVersion, versions, list?.latest)
            }
            override fun onFailure(call: Call<FirmwareListResponse>, t: Throwable) {
                toast("Could not reach server: ${t.localizedMessage ?: "network error"}")
            }
        })
    }

    /** Lists every uploaded firmware so each camera can get a different one. */
    private fun showVersionPicker(currentVersion: String, versions: List<FirmwareEntry>, latest: String?) {
        val labels = versions.map { v ->
            val tags = listOfNotNull(
                if (v.version == latest) "latest" else null,
                if (v.version == currentVersion) "running now" else null
            )
            val tagText = if (tags.isEmpty()) "" else "  (${tags.joinToString(", ")})"
            "${v.version}$tagText\n${v.size / 1024} KB  ·  ${v.uploadedAt.take(16)}"
        }.toTypedArray()

        AlertDialog.Builder(this)
            .setTitle("${nameText.text}: runs $currentVersion")
            .setItems(labels) { _, which -> confirmUpdate(currentVersion, versions[which]) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmUpdate(currentVersion: String, firmware: FirmwareEntry) {
        val note = if (firmware.version == currentVersion) "\n\nThe camera already runs this version." else ""
        confirm(
            "Update ${nameText.text} to ${firmware.version}?",
            "Camera runs: $currentVersion\nNew firmware: ${firmware.version} (${firmware.size / 1024} KB)\n" +
                "Uploaded: ${firmware.uploadedAt}\n\n" +
                "Only this camera is updated. Video stops for about a minute while it installs and restarts. " +
                "If the new firmware does not start properly, the camera returns to the old one by itself." + note,
            "Update"
        ) {
            sendCommand(api().startOta(cameraId, OtaRequest(firmware.version)), "Update started - watch Diagnostics") {
                sections[2].open()   // show progress in the Diagnostics card
            }
        }
    }

    private fun sendCommand(call: Call<CommandResponse>, okText: String, onOk: (() -> Unit)? = null) {
        call.enqueue(object : Callback<CommandResponse> {
            override fun onResponse(call: Call<CommandResponse>, response: Response<CommandResponse>) {
                if (response.isSuccessful) {
                    toast(okText)
                    onOk?.invoke()
                } else {
                    toast(NetworkUtils.readableError(response))
                }
            }
            override fun onFailure(call: Call<CommandResponse>, t: Throwable) {
                toast("Could not reach server: ${t.localizedMessage ?: "network error"}")
            }
        })
    }

    /** Human-readable firmware update status for the Diagnostics card. */
    private fun otaLabel(state: String?, progress: Int?, msg: String?): String = when (state) {
        "downloading", "uploading" -> "${(state ?: "").replaceFirstChar { it.uppercase() }} ${progress ?: 0}%"
        "installed"      -> "Installed, restarting"
        "pending_verify" -> "New firmware on trial"
        "rolled_back"    -> "Rolled back to previous"
        "failed"         -> "Failed (${msg ?: "?"})"
        else             -> if (!msg.isNullOrBlank()) "Idle ($msg)" else "—"
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /**
     * Extracts HH:mm:ss from a datetime string such as
     * "2026-09-21 14:32:15" or "2026-09-21T14:32:15" -> "14:32:15".
     */
    private fun extractTime(s: String): String =
        if (s.length >= 19) s.substring(11, 19) else s

    private fun fmt1(v: Double): String = String.format(Locale.US, "%.1f", v)

    /** Maps an RSSI dBm value to a human-readable signal quality label. */
    private fun rssiQuality(rssi: Int) = when {
        rssi >= -50 -> "Excellent"
        rssi >= -65 -> "Good"
        rssi >= -75 -> "Fair"
        rssi >= -85 -> "Weak"
        else        -> "Poor"
    }

    /** Rough labels for the ESP32 chip temperature (it reads well above room temperature). */
    private fun tempLevel(t: Double) = when {
        t < 45 -> "Normal"
        t < 60 -> "Warm"
        t < 70 -> "Hot"
        else   -> "Very hot"
    }

    /** 3725 -> "1h 2m", 75 -> "1m 15s", 40 -> "40s", 200000 -> "2d 7h". */
    private fun formatDuration(totalSeconds: Long): String {
        val s = totalSeconds.coerceAtLeast(0)
        val d = s / 86_400
        val h = (s % 86_400) / 3_600
        val m = (s % 3_600) / 60
        val sec = s % 60
        return when {
            d > 0 -> "${d}d ${h}h"
            h > 0 -> "${h}h ${m}m"
            m > 0 -> "${m}m ${sec}s"
            else  -> "${sec}s"
        }
    }

    /** Short, readable reason for the camera's last reboot. */
    private fun rebootLabel(cause: String?, reset: String?): String = when {
        cause == "wifi_lost"            -> "WiFi lost"
        cause == "mqtt_lost"            -> "MQTT lost"
        cause == "wifi_connect_timeout" -> "WiFi timeout"
        cause == "camera_init_fail"     -> "Camera init"
        cause == "camera_tint"          -> "Tint fix"
        cause == "ota_update"           -> "Update"
        cause == "ota_failed"           -> "Update failed"
        cause == "remote_reboot"        -> "Remote"
        cause == "rollback"             -> "Rollback"
        reset == "poweron"              -> "Power-on"
        reset == "task_wdt" || reset == "int_wdt" || reset == "wdt" -> "Watchdog"
        reset == "brownout"             -> "Brownout"
        reset == "panic"                -> "Crash"
        reset == "sw"                   -> "Restart"
        reset != null                   -> reset
        else                            -> "—"
    }

    /** ESP32 WiFi disconnect reason code -> text. 0/null = no data. */
    private fun discName(code: Int?): String = when (code) {
        null, 0 -> "—"
        1   -> "Unspecified (1)"
        2   -> "Auth expired (2)"
        3   -> "AP signed us out (3)"
        4   -> "Assoc expired (4)"
        5   -> "AP full (5)"
        8   -> "Camera left WiFi (8)"
        15  -> "Handshake timeout (15)"
        200 -> "Beacon timeout (200)"
        201 -> "AP not found (201)"
        202 -> "Auth failed (202)"
        203 -> "Assoc failed (203)"
        204 -> "Handshake timeout (204)"
        205 -> "Connection failed (205)"
        else -> "Code $code"
    }
}
