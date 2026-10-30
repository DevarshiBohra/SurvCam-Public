package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import org.example.survcam.R
import org.example.survcam.api.ApiClient
import org.example.survcam.storage.AppPreferences
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class SettingsActivity : BaseActivity() {
    private lateinit var preferences: AppPreferences
    private lateinit var serverUrlInput: EditText
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        preferences = AppPreferences(this)
        serverUrlInput = findViewById(R.id.serverUrlInput)
        statusText = findViewById(R.id.statusText)
        serverUrlInput.setText(preferences.getServerUrl())

        findViewById<Button>(R.id.saveButton).setOnClickListener {
            preferences.saveServerUrl(serverUrlInput.text.toString())
            statusText.text = "Saved."
        }
        findViewById<Button>(R.id.testButton).setOnClickListener { testConnection() }
        findViewById<Button>(R.id.logoutButton).setOnClickListener { logout() }
    }

    private fun logout() {
        preferences.clearSession()
        val intent = Intent(this, LoginActivity::class.java)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        startActivity(intent)
        finish()
    }

    private fun testConnection() {
        findViewById<Button>(R.id.saveButton).setOnClickListener {
            val url = serverUrlInput.text.toString().trim()
            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                statusText.text = "The URL must start with http:// or https://"
                return@setOnClickListener
            }
            preferences.saveServerUrl(url)
            statusText.text = "Saved."
        }
        
        val serverUrl = serverUrlInput.text.toString().trim().trimEnd('/')
        statusText.text = "Testing connection..."
        ApiClient.create(this, serverUrl).health().enqueue(object : Callback<ResponseBody> {
            override fun onResponse(call: Call<ResponseBody>, response: Response<ResponseBody>) {
                statusText.text = if (response.isSuccessful) "Connected." else "Server responded with ${response.code()}."
            }

            override fun onFailure(call: Call<ResponseBody>, t: Throwable) {
                statusText.text = "Could not connect: ${t.localizedMessage ?: "network error"}"
            }
        })
    }
}
