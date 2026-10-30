package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class RecordingHoursResponse(
    @SerializedName("camera_id") val cameraId: String,
    val date: String,
    val hours: List<HourSlot>
)

data class HourSlot(
    val hour: Int,
    @SerializedName("has_recording") val hasRecording: Boolean
)
