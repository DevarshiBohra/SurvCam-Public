package org.example.survcam.activities

import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.adapter.SnapshotHourAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.SnapshotHoursResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response
import java.text.SimpleDateFormat
import java.util.Locale

class SnapshotHoursActivity : BaseActivity() {

    private lateinit var cameraId:   String
    private lateinit var date:       String
    private lateinit var preferences: AppPreferences
    private lateinit var statusText: TextView
    private val adapter = SnapshotHourAdapter { hour ->
        startActivity(
            Intent(this, SnapshotListActivity::class.java)
                .putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
                .putExtra(Constants.EXTRA_DATE,       date)
                .putExtra(Constants.EXTRA_HOUR,       hour)
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_snapshot_hours)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        date        = intent.getStringExtra(Constants.EXTRA_DATE).orEmpty()
        preferences = AppPreferences(this)

        statusText = findViewById(R.id.statusText)
        findViewById<TextView>(R.id.titleText).text = formatDay(date)
        findViewById<RecyclerView>(R.id.hoursRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@SnapshotHoursActivity)
            adapter       = this@SnapshotHoursActivity.adapter
        }

        load()
    }

    private fun load() {
        statusText.text = "Loading hours…"
        ApiClient.create(this, preferences.getServerUrl())
            .snapshotHours(cameraId, date)
            .enqueue(object : Callback<SnapshotHoursResponse> {
                override fun onResponse(call: Call<SnapshotHoursResponse>, response: Response<SnapshotHoursResponse>) {
                    val hours = response.body()?.hours.orEmpty()
                    adapter.submit(hours)
                    statusText.text = if (hours.isEmpty()) "No snapshots for this day." else "${hours.size} hour(s) with snapshots"
                }
                override fun onFailure(call: Call<SnapshotHoursResponse>, t: Throwable) {
                    statusText.text = "Could not reach server"
                }
            })
    }

    private fun formatDay(dateStr: String): String = try {
        val date = SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(dateStr)!!
        SimpleDateFormat("yyyy-MM-dd  EEEE", Locale.US).format(date)
    } catch (_: Exception) { dateStr }
}
