package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.example.survcam.R
import org.example.survcam.adapter.DayAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.RecordingDaysResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.NetworkUtils
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class RecordingDaysActivity : BaseActivity() {
    private lateinit var cameraId: String
    private lateinit var preferences: AppPreferences
    private lateinit var adapter: DayAdapter
    private lateinit var statusText: TextView
    private lateinit var swipeRefresh: SwipeRefreshLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_recording_days)

        cameraId = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        preferences = AppPreferences(this)
        statusText = findViewById(R.id.statusText)
        swipeRefresh = findViewById(R.id.swipeRefresh)

        findViewById<TextView>(R.id.titleText).text = "Recordings: $cameraId"

        adapter = DayAdapter { date ->
            val intent = Intent(this, RecordingHoursActivity::class.java)
            intent.putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
            intent.putExtra(Constants.EXTRA_DATE, date)
            startActivity(intent)
        }

        findViewById<RecyclerView>(R.id.daysRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@RecordingDaysActivity)
            adapter = this@RecordingDaysActivity.adapter
        }

        swipeRefresh.setOnRefreshListener { loadDays() }
        loadDays()
    }

    private fun loadDays() {
        swipeRefresh.isRefreshing = true
        statusText.text = ""

        ApiClient.create(this, preferences.getServerUrl()).recordingDays(cameraId)
            .enqueue(object : Callback<RecordingDaysResponse> {
                override fun onResponse(call: Call<RecordingDaysResponse>, response: Response<RecordingDaysResponse>) {
                    swipeRefresh.isRefreshing = false
                    if (response.isSuccessful) {
                        val days = response.body()?.days.orEmpty()
                        adapter.submit(days)
                        statusText.text = if (days.isEmpty()) "No recordings found yet." else ""
                    } else {
                        statusText.text = NetworkUtils.readableError(response)
                    }
                }

                override fun onFailure(call: Call<RecordingDaysResponse>, t: Throwable) {
                    swipeRefresh.isRefreshing = false
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }
}
