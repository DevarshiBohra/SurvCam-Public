package org.example.survcam.api

import android.content.Context
import android.content.Intent
import org.example.survcam.activities.LoginActivity
import org.example.survcam.storage.AppPreferences
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    fun create(context: Context, serverUrl: String): ApiService {
        val appContext = context.applicationContext

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .addInterceptor(AuthInterceptor(appContext))
            .addInterceptor(logging)
            .addInterceptor(AuthResponseInterceptor(appContext))
            .build()

        return Retrofit.Builder()
            .baseUrl(serverUrl.trim().trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}

/**
 * Attaches the stored JWT (if any) to every outgoing request as
 * "Authorization: Bearer <token>". Requests made before login (e.g. the
 * /health check on the Server Setup screen) simply have no token yet,
 * which is fine since those routes don't require auth.
 */
private class AuthInterceptor(private val context: Context) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = AppPreferences(context).getToken()
        val original = chain.request()

        val request = if (!token.isNullOrBlank()) {
            original.newBuilder()
                .addHeader("Authorization", "Bearer $token")
                .build()
        } else {
            original
        }

        return chain.proceed(request)
    }
}

/**
 * Watches every response for a 401. If the token is missing, expired, or
 * the user was deactivated by an admin, this clears the stored session and
 * bounces the app back to LoginActivity instead of leaving a screen stuck
 * on a confusing "Not authenticated" error.
 *
 * The /auth/login and /auth/verify-2fa calls are excluded: a 401 there just
 * means "wrong password" or "wrong/expired code", which LoginActivity already
 * displays inline -
 * treating it as a session expiry would incorrectly redirect the user away
 * from the login screen they're already on.
 */
private class AuthResponseInterceptor(private val context: Context) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val response = chain.proceed(request)

        val path = request.url.encodedPath
        val isLoginRequest = path.endsWith("/auth/login") || path.endsWith("/auth/verify-2fa")

        if (response.code == 401 && !isLoginRequest) {
            AppPreferences(context).clearSession()

            val intent = Intent(context, LoginActivity::class.java)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            context.startActivity(intent)
        }

        return response
    }
}
