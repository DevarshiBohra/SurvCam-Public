package org.example.survcam.models

data class HealthResponse(
    val status: String?,
    val backend: String?,
    val mqtt: String?,
    val database: String?,
    val recording: String?,
    val time: String?
)
