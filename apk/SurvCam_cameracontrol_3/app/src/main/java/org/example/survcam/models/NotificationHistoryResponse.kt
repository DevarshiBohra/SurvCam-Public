package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class NotificationHistoryResponse(
    val notifications: List<NotificationItem>
)

data class NotificationItem(
    val id: Int,
    val type: String,
    @SerializedName("camera_id") val cameraId: String?,
    val title: String,
    val body: String,
    @SerializedName("created_at") val createdAt: String
)
