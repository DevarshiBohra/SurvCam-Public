package org.example.survcam.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import org.example.survcam.api.ApiClient
import org.example.survcam.models.DeviceTokenRequest
import org.example.survcam.storage.AppPreferences
import kotlin.concurrent.thread

class MuteActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val notificationId = intent.getIntExtra(NotificationHelper.EXTRA_NOTIFICATION_ID, -1)
        val pendingResult = goAsync()

        thread {
            try {
                val preferences = AppPreferences(context)
                val token = preferences.getFcmToken()

                if (
                    preferences.hasValidSession() &&
                    preferences.hasSavedServerUrl() &&
                    !token.isNullOrBlank()
                ) {
                    ApiClient.create(context, preferences.getServerUrl())
                        .muteNotifications(DeviceTokenRequest(token))
                        .execute()
                }
            } catch (e: Exception) {
                // Best effort - if this fails, the user can still mute from inside the app.
            } finally {
                if (notificationId != -1) {
                    NotificationManagerCompat.from(context).cancel(notificationId)
                }
                pendingResult.finish()
            }
        }
    }
}