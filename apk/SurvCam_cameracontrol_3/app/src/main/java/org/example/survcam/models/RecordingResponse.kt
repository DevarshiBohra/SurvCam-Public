package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class RecordingResponse(
    @SerializedName("camera_id") val cameraId: String?,
    val recordings: List<Recording>?
)
