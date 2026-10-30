package org.example.survcam.models

import com.google.gson.annotations.SerializedName
import java.io.Serializable

data class Camera(
    @SerializedName("camera_id") val cameraId: String,
    val name: String?,
    val status: String?,
    @SerializedName("ip_address") val ipAddress: String?,
    @SerializedName("last_seen") val lastSeen: String?,
    @SerializedName("created_at") val createdAt: String?,
    @SerializedName("updated_at") val updatedAt: String?
) : Serializable
