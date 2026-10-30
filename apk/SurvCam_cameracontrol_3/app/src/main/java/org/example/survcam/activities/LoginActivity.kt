package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import org.example.survcam.R
import org.example.survcam.api.ApiClient
import org.example.survcam.models.LoginChallengeResponse
import org.example.survcam.models.LoginRequest
import org.example.survcam.models.LoginResponse
import org.example.survcam.models.VerifyTwoFactorRequest
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.NetworkUtils
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

/**
 * Two-step login:
 *   1. username + password  -> the server sends a 6-digit code to Telegram
 *   2. that code            -> the server returns the access token
 */
class LoginActivity : BaseActivity() {
    private lateinit var preferences: AppPreferences
    private lateinit var usernameInput: EditText
    private lateinit var passwordInput: EditText
    private lateinit var statusText: TextView
    private lateinit var loginButton: Button
    private lateinit var changeServerButton: Button
    private lateinit var codeHintText: TextView
    private lateinit var codeInput: EditText
    private lateinit var verifyButton: Button
    private lateinit var backButton: Button

    /** Returned by the server after the password step; the code step needs it. */
    private var challengeId: String? = null

    companion object {
        private const val STATE_CHALLENGE = "challenge_id"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)

        preferences = AppPreferences(this)
        usernameInput = findViewById(R.id.usernameInput)
        passwordInput = findViewById(R.id.passwordInput)
        statusText = findViewById(R.id.statusText)
        loginButton = findViewById(R.id.loginButton)
        changeServerButton = findViewById(R.id.changeServerButton)
        codeHintText = findViewById(R.id.codeHintText)
        codeInput = findViewById(R.id.codeInput)
        verifyButton = findViewById(R.id.verifyButton)
        backButton = findViewById(R.id.backButton)

        findViewById<TextView>(R.id.serverUrlText).text = preferences.getServerUrl()

        changeServerButton.setOnClickListener {
            startActivity(Intent(this, ServerSetupActivity::class.java))
            finish()
        }

        loginButton.setOnClickListener { attemptLogin() }
        verifyButton.setOnClickListener { attemptVerify() }
        backButton.setOnClickListener { showCodeStep(false) }
        codeInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                attemptVerify()
                true
            } else {
                false
            }
        }

        // Screen was rotated while waiting for the code: go back to the code step.
        savedInstanceState?.getString(STATE_CHALLENGE)?.let {
            challengeId = it
            showCodeStep(true)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        challengeId?.let { outState.putString(STATE_CHALLENGE, it) }
    }

    /** Switches between the password form and the code form. Clears the status line. */
    private fun showCodeStep(show: Boolean) {
        listOf(usernameInput, passwordInput, loginButton, changeServerButton)
            .forEach { it.visibility = if (show) View.GONE else View.VISIBLE }
        listOf(codeHintText, codeInput, verifyButton, backButton)
            .forEach { it.visibility = if (show) View.VISIBLE else View.GONE }

        statusText.text = ""
        if (show) {
            codeInput.text.clear()
            codeInput.requestFocus()
        } else {
            challengeId = null
            passwordInput.text.clear()
        }
    }

    // ── Step 1: password ──────────────────────────────────────────────────────

    private fun attemptLogin() {
        val username = usernameInput.text.toString().trim()
        val password = passwordInput.text.toString()

        if (username.isBlank() || password.isBlank()) {
            statusText.text = "Enter username and password."
            return
        }

        loginButton.isEnabled = false
        statusText.text = "Logging in..."

        ApiClient.create(this, preferences.getServerUrl())
            .login(LoginRequest(username, password))
            .enqueue(object : Callback<LoginChallengeResponse> {
                override fun onResponse(call: Call<LoginChallengeResponse>, response: Response<LoginChallengeResponse>) {
                    loginButton.isEnabled = true

                    if (response.isSuccessful) {
                        val body = response.body()
                        if (body == null || body.challenge_id.isBlank()) {
                            statusText.text = "Unexpected response from server."
                            return
                        }

                        challengeId = body.challenge_id
                        showCodeStep(true)
                        statusText.text =
                            "A login code was sent to Telegram. It expires in ${body.expires_in / 60} minutes."
                    } else {
                        statusText.text = NetworkUtils.readableError(response)
                    }
                }

                override fun onFailure(call: Call<LoginChallengeResponse>, t: Throwable) {
                    loginButton.isEnabled = true
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }

    // ── Step 2: Telegram code ─────────────────────────────────────────────────

    private fun attemptVerify() {
        val id = challengeId
        if (id == null) {
            showCodeStep(false)
            return
        }

        val code = codeInput.text.toString().trim()
        if (code.length != 6) {
            statusText.text = "Enter the 6-digit code."
            return
        }

        verifyButton.isEnabled = false
        statusText.text = "Verifying..."

        ApiClient.create(this, preferences.getServerUrl())
            .verifyTwoFactor(VerifyTwoFactorRequest(id, code))
            .enqueue(object : Callback<LoginResponse> {
                override fun onResponse(call: Call<LoginResponse>, response: Response<LoginResponse>) {
                    verifyButton.isEnabled = true

                    if (response.isSuccessful) {
                        val body = response.body()
                        if (body == null) {
                            statusText.text = "Unexpected empty response from server."
                            return
                        }

                        preferences.saveSession(body.access_token, body.user.username, body.user.role)
                        startActivity(Intent(this@LoginActivity, CameraListActivity::class.java))
                        finish()
                    } else {
                        val message = NetworkUtils.readableError(response)

                        // A wrong digit can simply be retyped. An expired code, too many
                        // attempts or a disabled account means starting over.
                        val canRetry = response.code() != 401 || message == "Invalid login code"
                        if (canRetry) {
                            codeInput.text.clear()
                            statusText.text = message
                        } else {
                            showCodeStep(false)
                            statusText.text = "$message\nPlease log in again."
                        }
                    }
                }

                override fun onFailure(call: Call<LoginResponse>, t: Throwable) {
                    verifyButton.isEnabled = true
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }
}
