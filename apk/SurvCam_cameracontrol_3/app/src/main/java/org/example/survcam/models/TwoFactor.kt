package org.example.survcam.models

/** Step 1 of login: the password was accepted and a code was sent to Telegram. */
data class LoginChallengeResponse(
    val requires_2fa: Boolean,
    val challenge_id: String,
    val expires_in: Int
)

/** Step 2 of login: the 6-digit code the user received. */
data class VerifyTwoFactorRequest(
    val challenge_id: String,
    val code: String
)

/** What the signed-in account is allowed to do (decides whether Camera Control is shown). */
data class CapabilitiesResponse(
    val camera_control: Boolean
)
