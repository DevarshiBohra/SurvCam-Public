package org.example.survcam.models
import com.google.gson.annotations.SerializedName
data class SnapshotHoursResponse(
    @SerializedName("camera_id") val cameraId: String,
    val date: String,
    val hours: List<Int>
)
