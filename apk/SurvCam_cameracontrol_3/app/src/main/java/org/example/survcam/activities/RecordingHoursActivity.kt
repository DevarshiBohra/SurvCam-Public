package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.adapter.HourAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.RecordingHoursResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.NetworkUtils
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class RecordingHoursActivity : BaseActivity() {
    private lateinit var cameraId: String
    private lateinit var date: String
    private lateinit var preferences: AppPreferences
    private lateinit var adapter: HourAdapter
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_recording_hours)

        cameraId = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        date = intent.getStringExtra(Constants.EXTRA_DATE).orEmpty()
        preferences = AppPreferences(this)
        statusText = findViewById(R.id.statusText)

        findViewById<TextView>(R.id.titleText).text = date

        adapter = HourAdapter { hour ->
            val intent = Intent(this, NativePlaybackActivity::class.java)
            intent.putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
            intent.putExtra(Constants.EXTRA_DATE, date)
            intent.putExtra(Constants.EXTRA_HOUR, hour)
            startActivity(intent)
        }

        findViewById<RecyclerView>(R.id.hoursRecyclerView).apply {
            layoutManager = GridLayoutManager(this@RecordingHoursActivity, 2)
            adapter = this@RecordingHoursActivity.adapter
        }

        loadHours()
    }

    private fun loadHours() {
        statusText.text = "Loading..."
        ApiClient.create(this, preferences.getServerUrl()).recordingHours(cameraId, date)
            .enqueue(object : Callback<RecordingHoursResponse> {
                override fun onResponse(call: Call<RecordingHoursResponse>, response: Response<RecordingHoursResponse>) {
                    if (response.isSuccessful) {
                        val hours = response.body()?.hours.orEmpty()
                        adapter.submit(hours)
                        statusText.text = ""
                    } else {
                        statusText.text = NetworkUtils.readableError(response)
                    }
                }

                override fun onFailure(call: Call<RecordingHoursResponse>, t: Throwable) {
                    statusText.text = "Could not reach server: ${t.localizedMessage ?: "network error"}"
                }
            })
    }
}
