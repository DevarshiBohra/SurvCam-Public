package org.example.survcam.models

import com.google.gson.annotations.SerializedName

/** One heartbeat's worth of camera health data. Every field is nullable so
 *  cameras still running older firmware (without the new fields) don't break parsing. */
data class TelemetryReading(
    @SerializedName("recorded_at")      val recordedAt: String,
    @SerializedName("temp_c")           val tempC: Double?,
    @SerializedName("rssi")             val rssi: Int?,
    @SerializedName("free_heap")        val freeHeap: Long?,
    @SerializedName("uptime_ms")        val uptimeMs: Long?,
    @SerializedName("channel")          val channel: Int?,
    @SerializedName("bssid")            val bssid: String?,
    @SerializedName("ip_address")       val ipAddress: String?,
    @SerializedName("fw_version")       val fwVersion: String?,
    @SerializedName("reset_reason_str") val resetReason: String?,
    @SerializedName("restart_cause")    val restartCause: String?,
    @SerializedName("prev_disc_reason") val prevDiscReason: Int?,
    @SerializedName("disc_reason")      val discReason: Int?,
    @SerializedName("disc_count")       val discCount: Int?,
    @SerializedName("ota_state")        val otaState: String?,
    @SerializedName("ota_progress")     val otaProgress: Int?,
    @SerializedName("ota_part")         val otaPart: String?,
    @SerializedName("ota_msg")          val otaMsg: String?
)

data class TelemetryResponse(
    @SerializedName("camera_id") val cameraId: String,
    @SerializedName("readings")  val readings: List<TelemetryReading>
)

data class TelemetryLatestResponse(
    @SerializedName("camera_id") val cameraId: String,
    @SerializedName("latest")    val latest: TelemetryReading?
)

data class CameraEvent(
    @SerializedName("type")             val type: String,          // "reboot" or "dropout"
    @SerializedName("at")               val at: String,            // when the camera was last seen
    @SerializedName("duration_s")       val durationS: Long?,      // how long it was silent
    @SerializedName("restart_cause")    val restartCause: String?,
    @SerializedName("reset_reason")     val resetReason: String?,
    @SerializedName("prev_disc_reason") val prevDiscReason: Int?,
    @SerializedName("fw_version")       val fwVersion: String?
)

data class CameraEventsResponse(
    @SerializedName("camera_id") val cameraId: String,
    @SerializedName("hours")     val hours: Int,
    @SerializedName("events")    val events: List<CameraEvent>
)

data class FirmwareEntry(
    @SerializedName("version")     val version: String,
    @SerializedName("filename")    val filename: String,
    @SerializedName("size")        val size: Long,
    @SerializedName("sha256")      val sha256: String,
    @SerializedName("uploaded_at") val uploadedAt: String
)

data class FirmwareListResponse(
    @SerializedName("latest")   val latest: String?,
    @SerializedName("versions") val versions: List<FirmwareEntry>
)

/** version = null means "the latest uploaded firmware". */
data class OtaRequest(
    @SerializedName("version") val version: String?
)

data class CommandResponse(
    @SerializedName("message") val message: String?
)
