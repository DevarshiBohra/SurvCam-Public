package org.example.survcam.storage

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.example.survcam.utils.Constants

class AppPreferences(context: Context) {
    // v2: the login token is sensitive (it's effectively a long-lived
    // password substitute), so it's stored in EncryptedSharedPreferences
    // instead of plain SharedPreferences. If the device keystore is
    // somehow unavailable, fall back to plain prefs rather than crash.
    private val preferences: SharedPreferences = try {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        EncryptedSharedPreferences.create(
            context,
            "survcam_secure_preferences",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    } catch (e: Exception) {
        context.getSharedPreferences("survcam_preferences", Context.MODE_PRIVATE)
    }

    // --- Server URL (unchanged behavior from v1) ---

    fun getServerUrl(): String {
        return preferences.getString(KEY_SERVER_URL, Constants.DEFAULT_SERVER_URL)
            ?: Constants.DEFAULT_SERVER_URL
    }

    fun hasSavedServerUrl(): Boolean {
        return preferences.contains(KEY_SERVER_URL)
    }

    fun saveServerUrl(url: String) {
        preferences.edit().putString(KEY_SERVER_URL, normalizeUrl(url)).apply()
    }

    // --- Auth session (new in v2) ---

    fun saveSession(token: String, username: String, role: String) {
        preferences.edit()
            .putString(KEY_TOKEN, token)
            .putString(KEY_USERNAME, username)
            .putString(KEY_ROLE, role)
            .apply()
    }

    fun getToken(): String? = preferences.getString(KEY_TOKEN, null)

    fun getUsername(): String? = preferences.getString(KEY_USERNAME, null)

    fun getRole(): String? = preferences.getString(KEY_ROLE, null)

    fun hasValidSession(): Boolean = !getToken().isNullOrBlank()

    fun clearSession() {
        preferences.edit()
            .remove(KEY_TOKEN)
            .remove(KEY_USERNAME)
            .remove(KEY_ROLE)
            .apply()
    }

    fun saveFcmToken(token: String) {
        preferences.edit().putString(KEY_FCM_TOKEN, token).apply()
    }

    fun getFcmToken(): String? = preferences.getString(KEY_FCM_TOKEN, null)

    // --- Mute state (cached locally so the correct state shows instantly on app open) ---

    fun saveMutedUntil(mutedUntil: String?) {
        if (mutedUntil == null) preferences.edit().remove(KEY_MUTED_UNTIL).apply()
        else preferences.edit().putString(KEY_MUTED_UNTIL, mutedUntil).apply()
    }

    fun getMutedUntil(): String? = preferences.getString(KEY_MUTED_UNTIL, null)

    private fun normalizeUrl(url: String): String {
        return url.trim().trimEnd('/')
    }

    companion object {
        private const val KEY_SERVER_URL  = "server_url"
        private const val KEY_TOKEN       = "access_token"
        private const val KEY_USERNAME    = "username"
        private const val KEY_ROLE        = "role"
        private const val KEY_FCM_TOKEN   = "fcm_token"
        private const val KEY_MUTED_UNTIL = "muted_until"
    }
}
