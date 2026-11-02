package org.example.survcam.models

/** Live frames per second of each camera, as measured by the server. */
data class FpsResponse(
    val fps: Map<String, Int>
)
