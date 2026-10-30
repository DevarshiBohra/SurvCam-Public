package org.example.survcam.models
import com.google.gson.annotations.SerializedName
data class SnapshotDaysResponse(
    @SerializedName("camera_id") val cameraId: String,
    val days: List<String>
)
