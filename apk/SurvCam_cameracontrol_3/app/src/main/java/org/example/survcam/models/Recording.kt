package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class Recording(
    @SerializedName("camera_id") val cameraId: String?,
    val filename: String?,
    @SerializedName("relative_path") val relativePath: String?,
    @SerializedName("file_path") val filePath: String?,
    @SerializedName("size_bytes") val sizeBytes: Long?,
    @SerializedName("size_mb") val sizeMb: Double?,
    @SerializedName("modified_at") val modifiedAt: String?,
    @SerializedName("download_url") val downloadUrl: String?
)
