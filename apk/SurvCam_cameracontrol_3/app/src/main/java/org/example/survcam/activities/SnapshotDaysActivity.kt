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
import org.example.survcam.models.SnapshotDaysResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class SnapshotDaysActivity : BaseActivity() {

    private lateinit var cameraId:   String
    private lateinit var preferences: AppPreferences
    private lateinit var statusText: TextView
    private lateinit var swipe:      SwipeRefreshLayout
    private val adapter = DayAdapter { date ->
        startActivity(
            Intent(this, SnapshotHoursActivity::class.java)
                .putExtra(Constants.EXTRA_CAMERA_ID,   cameraId)
                .putExtra(Constants.EXTRA_DATE,         date)
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_snapshot_days)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        preferences = AppPreferences(this)

        val cameraName = intent.getStringExtra(Constants.EXTRA_CAMERA_NAME) ?: cameraId
        findViewById<TextView>(R.id.titleText).text = cameraName
        statusText = findViewById(R.id.statusText)
        swipe      = findViewById(R.id.swipeRefresh)

        findViewById<RecyclerView>(R.id.daysRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@SnapshotDaysActivity)
            adapter       = this@SnapshotDaysActivity.adapter
        }

        swipe.setOnRefreshListener { load() }
        load()
    }

    private fun load() {
        statusText.text = "Loading…"
        ApiClient.create(this, preferences.getServerUrl())
            .snapshotDays(cameraId)
            .enqueue(object : Callback<SnapshotDaysResponse> {
                override fun onResponse(call: Call<SnapshotDaysResponse>, response: Response<SnapshotDaysResponse>) {
                    swipe.isRefreshing = false
                    val days = response.body()?.days.orEmpty()
                    adapter.submit(days)
                    statusText.text = if (days.isEmpty()) "No snapshots found." else "${days.size} day(s) with snapshots"
                }
                override fun onFailure(call: Call<SnapshotDaysResponse>, t: Throwable) {
                    swipe.isRefreshing = false
                    statusText.text = "Could not reach server"
                }
            })
    }

}
