package org.example.survcam.models
import com.google.gson.annotations.SerializedName
data class SnapshotFilesResponse(
    @SerializedName("camera_id") val cameraId: String,
    val date: String,
    val hour: Int,
    val snapshots: List<String>
)
