package org.example.survcam.models

data class LoginResponse(
    val access_token: String,
    val token_type: String,
    val user: UserInfo
)

data class UserInfo(
    val username: String,
    val role: String
)
