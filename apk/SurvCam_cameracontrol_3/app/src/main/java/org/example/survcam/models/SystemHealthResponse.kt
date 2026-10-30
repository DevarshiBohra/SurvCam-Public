package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class SystemHealthResponse(
    @SerializedName("cameras_online") val camerasOnline: Int,
    @SerializedName("cameras_total") val camerasTotal: Int,
    val muted: Boolean,
    @SerializedName("muted_until") val mutedUntil: String?
)
