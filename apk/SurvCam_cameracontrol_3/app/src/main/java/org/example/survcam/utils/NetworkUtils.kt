package org.example.survcam.utils

import com.google.gson.JsonParser
import retrofit2.Response

object NetworkUtils {
    fun readableError(response: Response<*>): String {
        val fallback = "Server returned ${response.code()}"
        val raw = response.errorBody()?.string() ?: return fallback
        return try {
            JsonParser.parseString(raw).asJsonObject.get("detail")?.asString ?: fallback
        } catch (_: Exception) {
            fallback
        }
    }

    fun formatCameraDetails(cameraId: String, status: String?, ip: String?, lastSeen: String?): String {
        return "ID: $cameraId\nStatus: ${status ?: "unknown"}\nIP: ${ip ?: "not available"}\nLast seen: ${formatDate(lastSeen)}"
    }

    fun formatDate(value: String?): String {
        return value?.replace("T", " ")?.substringBefore(".") ?: "not available"
    }
}
