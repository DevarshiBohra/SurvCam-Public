package org.example.survcam.notifications

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.example.survcam.R
import org.example.survcam.activities.CameraDetailActivity
import org.example.survcam.activities.NotificationHistoryActivity
import org.example.survcam.utils.Constants
import kotlin.random.Random

object NotificationHelper {
    const val CHANNEL_CAMERA_STATUS = "camera_status"
    const val CHANNEL_MOTION = "motion"
    const val CHANNEL_GENERAL = "general"

    const val ACTION_MUTE = "org.example.survcam.ACTION_MUTE"
    const val EXTRA_NOTIFICATION_ID = "notification_id"

    fun show(context: Context, type: String, title: String, body: String, cameraId: String?) {
        val channelId = when (type) {
            "camera_down", "camera_recovered" -> CHANNEL_CAMERA_STATUS
            "motion" -> CHANNEL_MOTION
            else -> CHANNEL_GENERAL
        }

        val notificationId = Random.nextInt()

        // Tapping the notification opens the relevant camera directly if we
        // know which one it's about, otherwise falls back to the
        // notification history screen.
        val contentIntent = if (!cameraId.isNullOrBlank()) {
            Intent(context, CameraDetailActivity::class.java).apply {
                putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
        } else {
            Intent(context, NotificationHistoryActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        }

        val contentPendingIntent = PendingIntent.getActivity(
            context, notificationId, contentIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val builder = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(contentPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        // Motion alerts get a one-tap "Mute for 1 hour" action right on the
        // notification, so a false alarm (staff working late) can be
        // silenced from the lock screen without opening the app.
        if (type == "motion") {
            val muteIntent = Intent(context, MuteActionReceiver::class.java).apply {
                action = ACTION_MUTE
                putExtra(EXTRA_NOTIFICATION_ID, notificationId)
            }
            val mutePendingIntent = PendingIntent.getBroadcast(
                context, notificationId, muteIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            builder.addAction(0, "Mute for 1 hour", mutePendingIntent)
        }

        NotificationManagerCompat.from(context).notify(notificationId, builder.build())
    }
}
