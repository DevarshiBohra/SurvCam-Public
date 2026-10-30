package org.example.survcam.activities

import android.os.Bundle
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.adapter.NotificationAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.NotificationHistoryResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.NetworkUtils
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class NotificationHistoryActivity : BaseActivity() {
    private lateinit var preferences: AppPreferences
    private lateinit var adapter: NotificationAdapter
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_notification_history)

        preferences = AppPreferences(this)
        statusText = findViewById(R.id.statusText)
        adapter = NotificationAdapter()

        findViewById<RecyclerView>(R.id.notificationRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@NotificationHistoryActivity)
            adapter = this@NotificationHistoryActivity.adapter
        }

        loadHistory()
    }

    private fun loadHistory() {
        statusText.text = "Loading..."
        ApiClient.create(this, preferences.getServerUrl()).notificationHistory(100)
            .enqueue(object : Callback<NotificationHistoryResponse> {
                override fun onResponse(call: Call<NotificationHistoryResponse>, response: Response<NotificationHistoryResponse>) {
                    if (response.isSuccessful) {
                        val items = response.body()?.notifications.orEmpty()
                        adapter.submit(items)
                        statusText.text = if (items.isEmpty()) "No notifications yet." else ""
                    } else {
                        statusText.text = NetworkUtils.readableError(response)
                    }
                }

                override fun onFailure(call: Call<NotificationHistoryResponse>, t: Throwable) {
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }
}
