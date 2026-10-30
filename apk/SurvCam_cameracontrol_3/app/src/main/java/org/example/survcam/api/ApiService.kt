package org.example.survcam.api

import org.example.survcam.models.Camera
import org.example.survcam.models.CameraResponse
import org.example.survcam.models.DeviceTokenRequest
import org.example.survcam.models.SnapshotDaysResponse
import org.example.survcam.models.SnapshotHoursResponse
import org.example.survcam.models.SnapshotFilesResponse
import retrofit2.http.Streaming
import org.example.survcam.models.LoginRequest
import org.example.survcam.models.LoginResponse
import org.example.survcam.models.MuteStatusResponse
import org.example.survcam.models.NotificationHistoryResponse
import org.example.survcam.models.RecordingDaysResponse
import org.example.survcam.models.RecordingHoursResponse
import org.example.survcam.models.RecordingManifestResponse
import org.example.survcam.models.RecordingResponse
import org.example.survcam.models.SystemHealthResponse
import org.example.survcam.models.RssiResponse
import org.example.survcam.models.TelemetryResponse
import org.example.survcam.models.TelemetryLatestResponse
import org.example.survcam.models.CameraEventsResponse
import org.example.survcam.models.CapabilitiesResponse
import org.example.survcam.models.LoginChallengeResponse
import org.example.survcam.models.VerifyTwoFactorRequest
import org.example.survcam.models.CommandResponse
import org.example.survcam.models.FirmwareListResponse
import org.example.survcam.models.OtaRequest
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface ApiService {
    @GET("health")
    fun health(): Call<ResponseBody>

    // Login is two steps: password -> code sent to Telegram -> code verified -> token
    @POST("auth/login")
    fun login(@Body request: LoginRequest): Call<LoginChallengeResponse>

    @POST("auth/verify-2fa")
    fun verifyTwoFactor(@Body request: VerifyTwoFactorRequest): Call<LoginResponse>

    @GET("auth/capabilities")
    fun capabilities(): Call<CapabilitiesResponse>

    @GET("cameras")
    fun cameras(): Call<CameraResponse>

    @GET("cameras/{cameraId}")
    fun camera(@Path("cameraId") cameraId: String): Call<Camera>

    @GET("recordings/{cameraId}")
    fun recordings(@Path("cameraId") cameraId: String): Call<RecordingResponse>

    @GET("recordings/{cameraId}/days")
    fun recordingDays(@Path("cameraId") cameraId: String): Call<RecordingDaysResponse>

    @GET("recordings/{cameraId}/days/{date}/hours")
    fun recordingHours(
        @Path("cameraId") cameraId: String,
        @Path("date") date: String
    ): Call<RecordingHoursResponse>

    @GET("recordings/{cameraId}/manifest/{date}/{hour}")
    fun recordingManifest(
        @Path("cameraId") cameraId: String,
        @Path("date") date: String,
        @Path("hour") hour: Int
    ): Call<RecordingManifestResponse>

    @POST("notifications/register-token")
    fun registerDeviceToken(@Body request: DeviceTokenRequest): Call<ResponseBody>

    @POST("notifications/mute")
    fun muteNotifications(@Body request: DeviceTokenRequest): Call<MuteStatusResponse>

    /** Variable-duration mute — used by the in-app picker.
     *  MuteActionReceiver keeps calling [muteNotifications] (no hours param → server defaults to 1 h). */
    @POST("notifications/mute")
    fun muteNotificationsForHours(
        @Body request: DeviceTokenRequest,
        @Query("hours") hours: Int
    ): Call<MuteStatusResponse>

    @POST("notifications/unmute")
    fun unmuteNotifications(@Body request: DeviceTokenRequest): Call<MuteStatusResponse>

    @POST("notifications/mute-status")
    fun muteStatus(@Body request: DeviceTokenRequest): Call<MuteStatusResponse>

    @GET("notifications/history")
    fun notificationHistory(@Query("limit") limit: Int = 100): Call<NotificationHistoryResponse>

    @GET("cameras/{cameraId}/rssi")
    fun rssiHistory(
        @Path("cameraId") cameraId: String,
        @Query("limit")   limit: Int = 20
    ): Call<RssiResponse>

    @GET("cameras/{cameraId}/telemetry")
    fun telemetryHistory(
        @Path("cameraId") cameraId: String,
        @Query("limit")   limit: Int = 20
    ): Call<TelemetryResponse>

    @GET("cameras/{cameraId}/telemetry/latest")
    fun telemetryLatest(@Path("cameraId") cameraId: String): Call<TelemetryLatestResponse>

    @GET("cameras/{cameraId}/events")
    fun cameraEvents(
        @Path("cameraId") cameraId: String,
        @Query("hours")   hours: Int = 168
    ): Call<CameraEventsResponse>

    @GET("firmware")
    fun firmwareList(@Query("camera_id") cameraId: String?): Call<FirmwareListResponse>

    @POST("cameras/{cameraId}/ota")
    fun startOta(
        @Path("cameraId") cameraId: String,
        @Body body: OtaRequest
    ): Call<CommandResponse>

    @POST("cameras/{cameraId}/reboot")
    fun rebootCamera(@Path("cameraId") cameraId: String): Call<CommandResponse>

    @POST("cameras/{cameraId}/rollback")
    fun rollbackCamera(@Path("cameraId") cameraId: String): Call<CommandResponse>

    @GET("snapshots/{cameraId}/days")
    fun snapshotDays(@Path("cameraId") cameraId: String): Call<SnapshotDaysResponse>

    @GET("snapshots/{cameraId}/days/{date}/hours")
    fun snapshotHours(
        @Path("cameraId") cameraId: String,
        @Path("date")     date: String
    ): Call<SnapshotHoursResponse>

    @GET("snapshots/{cameraId}/days/{date}/hours/{hour}")
    fun snapshotFiles(
        @Path("cameraId") cameraId: String,
        @Path("date")     date: String,
        @Path("hour")     hour: Int
    ): Call<SnapshotFilesResponse>

    @Streaming
    @GET("snapshots/{cameraId}/file/{date}/{filename}")
    fun snapshotImage(
        @Path("cameraId") cameraId: String,
        @Path("date")     date: String,
        @Path("filename") filename: String
    ): Call<ResponseBody>

    @GET("system/health")
    fun systemHealth(): Call<SystemHealthResponse>
}
