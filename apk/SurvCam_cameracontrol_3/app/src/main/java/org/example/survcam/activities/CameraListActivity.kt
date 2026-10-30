package org.example.survcam.activities

import android.Manifest
import android.app.Dialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Paint
import android.graphics.drawable.ColorDrawable
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Button
import android.widget.NumberPicker
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.adapter.CameraAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.CameraResponse
import org.example.survcam.models.DeviceTokenRequest
import org.example.survcam.models.MuteStatusResponse
import org.example.survcam.models.SystemHealthResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.NetworkUtils
import com.google.firebase.messaging.FirebaseMessaging
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class CameraListActivity : BaseActivity() {

    private lateinit var preferences: AppPreferences
    private lateinit var adapter: CameraAdapter
    private lateinit var messageText: TextView
    private lateinit var healthText: TextView
    private lateinit var muteButton: Button
    private lateinit var muteUntilText: TextView

    private var currentlyMuted = false

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    // ── Lifecycle ──────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_camera_list)

        preferences  = AppPreferences(this)
        messageText  = findViewById(R.id.messageText)
        healthText   = findViewById(R.id.healthText)
        muteButton   = findViewById(R.id.muteButton)
        muteUntilText = findViewById(R.id.muteUntilText)

        adapter = CameraAdapter { camera ->
            startActivity(
                Intent(this, CameraDetailActivity::class.java)
                    .putExtra(Constants.EXTRA_CAMERA_ID,   camera.cameraId)
                    .putExtra(Constants.EXTRA_CAMERA_NAME, camera.name ?: camera.cameraId)
            )
        }

        findViewById<RecyclerView>(R.id.cameraRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@CameraListActivity)
            adapter = this@CameraListActivity.adapter
        }

        findViewById<Button>(R.id.refreshButton).setOnClickListener { loadCameras() }
        findViewById<Button>(R.id.multiViewButton).setOnClickListener {
            startActivity(Intent(this, MultiCameraActivity::class.java))
        }
        findViewById<Button>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<Button>(R.id.notificationsButton).setOnClickListener {
            startActivity(Intent(this, NotificationHistoryActivity::class.java))
        }

        muteButton.setOnClickListener {
            if (currentlyMuted) {
                // Already muted → unmute immediately, no picker needed
                performUnmute()
            } else {
                // Not muted → show the duration picker wheel
                showMutePicker()
            }
        }

        requestNotificationPermissionIfNeeded()
        registerFcmToken()
        loadCameras()
    }

    override fun onResume() {
        super.onResume()
        restoreMuteFromPrefs()      // instant — no network needed
        loadCameras()
        loadHealth()
        loadDeviceMuteStatus()      // independent of loadHealth() chain
    }

    // ── Mute duration picker ───────────────────────────────────────────────────

    private fun showMutePicker() {
        val dialog = Dialog(this)
        dialog.setContentView(R.layout.dialog_mute_picker)

        // Transparent window so the FrameLayout root is invisible and only the
        // inner card shows; the dim is handled by FLAG_DIM_BEHIND.
        dialog.window?.apply {
            setBackgroundDrawableResource(android.R.color.transparent)
            setLayout(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT
            )
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            setDimAmount(0.7f)   // dims the app behind the picker
        }

        // ── NumberPicker setup ───────────────────────────────────────────────

        val picker = dialog.findViewById<NumberPicker>(R.id.hoursPicker)
        val labels = Array(12) { i -> if (i == 0) "1 hr" else "${i + 1} hrs" }
        picker.minValue = 0
        picker.maxValue = 11
        picker.displayedValues = labels
        picker.wrapSelectorWheel = false
        styleNumberPicker(picker)

        // ── Buttons ──────────────────────────────────────────────────────────

        dialog.findViewById<Button>(R.id.confirmMuteButton).setOnClickListener {
            val selectedHours = picker.value + 1   // 0–11 → 1–12 hours
            dialog.dismiss()

            // Calculate end time locally and update the UI immediately —
            // no waiting for the server round-trip.
            val cal = java.util.Calendar.getInstance()
            cal.add(java.util.Calendar.HOUR_OF_DAY, selectedHours)
            val isoFmt = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", java.util.Locale.US)
            val mutedUntilIso = isoFmt.format(cal.time)
            preferences.saveMutedUntil(mutedUntilIso)
            applyMuteUi(true, mutedUntilIso)

            // Persist on the server in the background
            performMuteForHours(selectedHours)
        }
        dialog.findViewById<Button>(R.id.cancelPickerButton).setOnClickListener {
            dialog.dismiss()
        }

        dialog.show()
    }

    /**
     * Applies dark-theme styling to a NumberPicker.
     * Uses the API-29 setter where available; falls back to reflection on API 26–28.
     */
    private fun styleNumberPicker(picker: NumberPicker) {
        val textColor = ContextCompat.getColor(this, R.color.text_main)
        val accentColor = ContextCompat.getColor(this, R.color.accent)

        picker.setBackgroundColor(android.graphics.Color.TRANSPARENT)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            picker.textColor = textColor
        } else {
            // Reflection for API 26–28
            try {
                val paintField = NumberPicker::class.java.getDeclaredField("mSelectorWheelPaint")
                paintField.isAccessible = true
                (paintField.get(picker) as? Paint)?.color = textColor
            } catch (_: Exception) {}

            try {
                val inputField = NumberPicker::class.java.getDeclaredField("mInputText")
                inputField.isAccessible = true
                val editText = inputField.get(picker) as? android.widget.EditText
                editText?.setTextColor(textColor)
            } catch (_: Exception) {}
        }

        // Accent-coloured selection dividers (the two lines flanking the selected value)
        try {
            val dividerField = NumberPicker::class.java.getDeclaredField("mSelectionDivider")
            dividerField.isAccessible = true
            dividerField.set(picker, ColorDrawable(accentColor))
        } catch (_: Exception) {}

        picker.invalidate()
    }

    // ── Mute / unmute API calls ────────────────────────────────────────────────

    private fun performMuteForHours(hours: Int) {
        val token = preferences.getFcmToken()
        if (token.isNullOrBlank()) { healthText.text = "Notification token not ready"; return }

        ApiClient.create(this, preferences.getServerUrl())
            .muteNotificationsForHours(DeviceTokenRequest(token), hours)
            .enqueue(object : Callback<MuteStatusResponse> {
                override fun onResponse(call: Call<MuteStatusResponse>, response: Response<MuteStatusResponse>) {
                    val body = response.body()
                    if (response.isSuccessful && body != null) {
                        // Update button and mute-until label immediately from this
                        // response — don't rely on the loadHealth() → loadDeviceMuteStatus()
                        // chain, which can silently fail and leave the UI stale.
                        applyMuteUi(body.muted, body.mutedUntil)
                    }
                    loadHealth()   // still refresh the camera count / health text
                }
                override fun onFailure(call: Call<MuteStatusResponse>, t: Throwable) {
                    healthText.text = "Could not update mute state"
                }
            })
    }

    private fun performUnmute() {
        val token = preferences.getFcmToken()
        if (token.isNullOrBlank()) { healthText.text = "Notification token not ready"; return }

        // Update UI immediately — don't wait for the server
        preferences.saveMutedUntil(null)
        applyMuteUi(false, null)

        ApiClient.create(this, preferences.getServerUrl())
            .unmuteNotifications(DeviceTokenRequest(token))
            .enqueue(object : Callback<MuteStatusResponse> {
                override fun onResponse(call: Call<MuteStatusResponse>, response: Response<MuteStatusResponse>) {
                    loadHealth()
                }
                override fun onFailure(call: Call<MuteStatusResponse>, t: Throwable) {
                    healthText.text = "Could not update mute state"
                }
            })
    }

    /**
     * Reads the locally-stored muted_until timestamp and applies the correct
     * mute UI immediately — no network call needed. Called first in onResume
     * so the button shows "Unmute" / red text before the server responds.
     */
    private fun restoreMuteFromPrefs() {
        val mutedUntil = preferences.getMutedUntil()
        if (mutedUntil == null) {
            applyMuteUi(false, null)
            return
        }
        try {
            val sdf  = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            val date = sdf.parse(mutedUntil.take(19).replace('T', ' '))
            if (date != null && date.after(java.util.Date())) {
                applyMuteUi(true, mutedUntil)   // still within mute window
            } else {
                preferences.saveMutedUntil(null) // expired — clear it
                applyMuteUi(false, null)
            }
        } catch (_: Exception) {
            applyMuteUi(false, null)
        }
    }

    /** Single place that updates the mute button label and the red mute-until line. */
    private fun applyMuteUi(muted: Boolean, mutedUntil: String?) {
        currentlyMuted    = muted
        muteButton.text   = if (muted) "Unmute" else "Mute"
        if (muted && !mutedUntil.isNullOrBlank()) {
            muteUntilText.visibility = android.view.View.VISIBLE
            muteUntilText.text       = "Muted until ${formatMutedUntil(mutedUntil)}"
        } else {
            muteUntilText.visibility = android.view.View.GONE
        }
    }

    /** Formats an ISO datetime string from the server to a human-readable time, e.g. "5:51 PM". */
    private fun formatMutedUntil(iso: String): String = try {
        // Handle both "2026-09-21T17:51:00" and "2026-09-21 17:51:00" (with or without microseconds)
        val normalised = iso.take(19).replace('T', ' ')
        val sdf  = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
        val date = sdf.parse(normalised)!!
        java.text.SimpleDateFormat("h:mm a", java.util.Locale.US).format(date)
    } catch (_: Exception) {
        iso.take(16)   // fallback: raw string truncated
    }

    // ── Data loading ───────────────────────────────────────────────────────────

    private fun loadCameras() {
        messageText.text = "Loading cameras..."
        ApiClient.create(this, preferences.getServerUrl())
            .cameras()
            .enqueue(object : Callback<CameraResponse> {
                override fun onResponse(call: Call<CameraResponse>, response: Response<CameraResponse>) {
                    if (response.isSuccessful) {
                        val cameras = response.body()?.cameras.orEmpty()
                        adapter.submit(cameras)
                        messageText.text = if (cameras.isEmpty()) "No cameras found."
                                           else "${cameras.size} camera(s) found."
                    } else {
                        messageText.text = NetworkUtils.readableError(response)
                    }
                }
                override fun onFailure(call: Call<CameraResponse>, t: Throwable) {
                    messageText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }

    private fun loadHealth() {
        ApiClient.create(this, preferences.getServerUrl())
            .systemHealth()
            .enqueue(object : Callback<SystemHealthResponse> {
                override fun onResponse(call: Call<SystemHealthResponse>, response: Response<SystemHealthResponse>) {
                    val body = response.body() ?: return
                    healthText.text = "${body.camerasOnline}/${body.camerasTotal} cameras online"
                }
                override fun onFailure(call: Call<SystemHealthResponse>, t: Throwable) {
                    healthText.text = "Status unavailable"
                }
            })
    }

    private fun loadDeviceMuteStatus() {
        val token = preferences.getFcmToken()
        // If the token isn't ready yet, leave the UI as-is (prefs already set it).
        if (token.isNullOrBlank()) return

        ApiClient.create(this, preferences.getServerUrl())
            .muteStatus(DeviceTokenRequest(token))
            .enqueue(object : Callback<MuteStatusResponse> {
                override fun onResponse(call: Call<MuteStatusResponse>, response: Response<MuteStatusResponse>) {
                    val body = response.body() ?: return
                    // Server is the source of truth — sync local prefs to match
                    preferences.saveMutedUntil(if (body.muted) body.mutedUntil else null)
                    val baseText = healthText.text.toString().substringBefore(" - Notifications")
                    healthText.text = if (body.muted) "$baseText - Notifications muted" else baseText
                    applyMuteUi(body.muted, body.mutedUntil)
                }
                override fun onFailure(call: Call<MuteStatusResponse>, t: Throwable) {
                    // Network failed — leave current UI state intact (prefs-based state is fine)
                }
            })
    }

    // ── Permissions / FCM ──────────────────────────────────────────────────────

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun registerFcmToken() {
        try {
            FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
                if (!task.isSuccessful) return@addOnCompleteListener
                val token = task.result ?: return@addOnCompleteListener
                preferences.saveFcmToken(token)
                ApiClient.create(this, preferences.getServerUrl())
                    .registerDeviceToken(DeviceTokenRequest(token))
                    .enqueue(object : Callback<ResponseBody> {
                        override fun onResponse(call: Call<ResponseBody>, response: Response<ResponseBody>) {}
                        override fun onFailure(call: Call<ResponseBody>, t: Throwable) {}
                    })
            }
        } catch (_: Exception) {}
    }
}
