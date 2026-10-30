package org.example.survcam.notifications

import org.example.survcam.api.ApiClient
import org.example.survcam.models.DeviceTokenRequest
import org.example.survcam.storage.AppPreferences
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class SurvCamFirebaseMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        registerTokenIfLoggedIn(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)

        // Motion alerts arrive as data-only messages (so this always fires,
        // even in the background, letting us attach the mute action
        // button). camera_down/camera_recovered arrive as a notification+
        // data hybrid, which the system displays automatically when the
        // app is backgrounded - this callback only fires for those while
        // the app is in the foreground, which is fine since they don't
        // need a custom action button.
        val data = message.data
        val type = data["type"] ?: return
        val title = data["title"] ?: message.notification?.title ?: "SurvCam"
        val body = data["body"] ?: message.notification?.body ?: ""
        val cameraId = data["camera_id"]

        NotificationHelper.show(applicationContext, type, title, body, cameraId)
    }

    private fun registerTokenIfLoggedIn(token: String) {
        try {
            val preferences = AppPreferences(applicationContext)
            if (!preferences.hasValidSession() || !preferences.hasSavedServerUrl()) return

            ApiClient.create(applicationContext, preferences.getServerUrl())
                .registerDeviceToken(DeviceTokenRequest(token))
                .enqueue(object : Callback<ResponseBody> {
                    override fun onResponse(call: Call<ResponseBody>, response: Response<ResponseBody>) {}
                    override fun onFailure(call: Call<ResponseBody>, t: Throwable) {}
                })
        } catch (e: Exception) {
            // Best effort - a token refresh failing to register shouldn't
            // crash anything; it'll be retried on next login or app open.
        }
    }
}
