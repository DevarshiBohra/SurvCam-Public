package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class RecordingDaysResponse(
    @SerializedName("camera_id") val cameraId: String,
    val days: List<String>
)
