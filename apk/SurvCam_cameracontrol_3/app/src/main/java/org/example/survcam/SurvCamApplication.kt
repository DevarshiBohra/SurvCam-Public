package org.example.survcam

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import org.example.survcam.notifications.NotificationHelper

class SurvCamApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NotificationManager::class.java)

        manager.createNotificationChannel(
            NotificationChannel(
                NotificationHelper.CHANNEL_CAMERA_STATUS,
                "Camera Status",
                NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "Camera offline and recovery alerts" }
        )

        manager.createNotificationChannel(
            NotificationChannel(
                NotificationHelper.CHANNEL_MOTION,
                "Motion Detected",
                NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "After-hours motion alerts" }
        )

        manager.createNotificationChannel(
            NotificationChannel(
                NotificationHelper.CHANNEL_GENERAL,
                "General",
                NotificationManager.IMPORTANCE_DEFAULT
            )
        )
    }
}
