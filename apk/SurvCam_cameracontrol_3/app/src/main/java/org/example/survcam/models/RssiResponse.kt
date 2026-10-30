package org.example.survcam.models

import com.google.gson.annotations.SerializedName

data class RssiReading(
    @SerializedName("recorded_at") val recordedAt: String,
    @SerializedName("rssi")        val rssi: Int?
)

data class RssiResponse(
    @SerializedName("camera_id") val cameraId: String,
    @SerializedName("readings")  val readings: List<RssiReading>
)
