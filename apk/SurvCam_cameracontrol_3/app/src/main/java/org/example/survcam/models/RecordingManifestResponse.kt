package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class RecordingManifestResponse(
    @SerializedName("camera_id") val cameraId: String,
    val date: String,
    val hour: Int,
    val segments: List<RecordingSegment>
)

data class RecordingSegment(
    val filename: String,
    @SerializedName("relative_path") val relativePath: String,
    @SerializedName("start_time") val startTime: String,
    @SerializedName("duration_seconds") val durationSeconds: Double,
    @SerializedName("download_url") val downloadUrl: String
)
