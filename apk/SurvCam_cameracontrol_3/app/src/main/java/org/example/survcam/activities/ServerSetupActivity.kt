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

class ServerSetupActivity : BaseActivity() {
    private lateinit var preferences: AppPreferences
    private lateinit var serverUrlInput: EditText
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_server_setup)

        preferences = AppPreferences(this)
        serverUrlInput = findViewById(R.id.serverUrlInput)
        statusText = findViewById(R.id.statusText)
        serverUrlInput.setText(preferences.getServerUrl())

        findViewById<Button>(R.id.testButton).setOnClickListener { testConnection(false) }
        findViewById<Button>(R.id.saveButton).setOnClickListener { testConnection(true) }
    }

    private fun testConnection(continueOnSuccess: Boolean) {
        val serverUrl = serverUrlInput.text.toString().trim().trimEnd('/')
        if (serverUrl.isBlank()) {
            statusText.text = "Enter a server URL."
            return
        }

        if (!serverUrl.startsWith("http://") && !serverUrl.startsWith("https://")) {
            statusText.text = "The URL must start with http:// or https://"
            return
        }

        statusText.text = "Testing connection..."
        ApiClient.create(this, serverUrl).health().enqueue(object : Callback<ResponseBody> {
            override fun onResponse(call: Call<ResponseBody>, response: Response<ResponseBody>) {
                if (response.isSuccessful) {
                    preferences.saveServerUrl(serverUrl)
                    statusText.text = "Connected to server."
                    if (continueOnSuccess) {
                        startActivity(Intent(this@ServerSetupActivity, LoginActivity::class.java))
                        finish()
                    }
                } else {
                    statusText.text = "Server responded with ${response.code()}."
                }
            }

            override fun onFailure(call: Call<ResponseBody>, t: Throwable) {
                statusText.text = "Could not connect: ${t.localizedMessage ?: "network error"}"
            }
        })
    }
}
