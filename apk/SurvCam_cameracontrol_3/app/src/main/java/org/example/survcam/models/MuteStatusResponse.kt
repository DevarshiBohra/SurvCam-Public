package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class MuteStatusResponse(
    val muted: Boolean,
    @SerializedName("muted_until") val mutedUntil: String?
)
