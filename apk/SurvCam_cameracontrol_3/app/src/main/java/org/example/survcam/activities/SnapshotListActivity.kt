package org.example.survcam.activities

import android.content.ContentValues
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.adapter.SnapshotAdapter
import org.example.survcam.api.ApiClient
import org.example.survcam.models.SnapshotFilesResponse
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import okhttp3.ResponseBody
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale

class SnapshotListActivity : BaseActivity() {

    private lateinit var cameraId:    String
    private lateinit var date:        String
    private var hour:                 Int = 0
    private lateinit var preferences: AppPreferences
    private lateinit var statusText:  TextView
    private lateinit var downloadBar: LinearLayout
    private lateinit var selectionCountText: TextView
    private val adapter = SnapshotAdapter(
        onItemClick = { filename ->
            startActivity(
                Intent(this, SnapshotViewerActivity::class.java)
                    .putExtra(Constants.EXTRA_CAMERA_ID, cameraId)
                    .putExtra(Constants.EXTRA_DATE,       date)
                    .putExtra(Constants.EXTRA_FILENAME,   filename)
            )
        },
        onSelectionChanged = { count -> onSelectionChanged(count) }
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_snapshot_list)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        date        = intent.getStringExtra(Constants.EXTRA_DATE).orEmpty()
        hour        = intent.getIntExtra(Constants.EXTRA_HOUR, 0)
        preferences = AppPreferences(this)

        statusText       = findViewById(R.id.statusText)
        downloadBar      = findViewById(R.id.downloadBar)
        selectionCountText = findViewById(R.id.selectionCountText)

        val hourLabel = formatHour(hour)
        val dayLabel  = formatDay(date)
        findViewById<TextView>(R.id.titleText).text = "$dayLabel  ·  $hourLabel"

        findViewById<RecyclerView>(R.id.snapshotRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@SnapshotListActivity)
            adapter       = this@SnapshotListActivity.adapter
        }

        findViewById<Button>(R.id.downloadButton).setOnClickListener { downloadSelected() }
        findViewById<Button>(R.id.cancelSelectionButton).setOnClickListener {
            adapter.exitSelectionMode()
        }

        load()
    }

    private fun load() {
        statusText.text = "Loading snapshots…"
        ApiClient.create(this, preferences.getServerUrl())
            .snapshotFiles(cameraId, date, hour)
            .enqueue(object : Callback<SnapshotFilesResponse> {
                override fun onResponse(call: Call<SnapshotFilesResponse>, response: Response<SnapshotFilesResponse>) {
                    val files = response.body()?.snapshots.orEmpty()
                    adapter.submit(files)
                    statusText.text = when {
                        files.isEmpty() -> "No snapshots in this hour."
                        else -> "${files.size} snapshot(s)  ·  long-press to select"
                    }
                }
                override fun onFailure(call: Call<SnapshotFilesResponse>, t: Throwable) {
                    statusText.text = "Could not reach server"
                }
            })
    }

    private fun onSelectionChanged(count: Int) {
        if (count == 0) {
            downloadBar.visibility = View.GONE
        } else {
            downloadBar.visibility = View.VISIBLE
            selectionCountText.text = "$count selected"
        }
    }

    // ── Batch download ─────────────────────────────────────────────────────────

    private fun downloadSelected() {
        val filenames = adapter.getSelectedItems().toList()
        if (filenames.isEmpty()) return
        adapter.exitSelectionMode()

        statusText.text = "Downloading ${filenames.size} snapshot(s)…"

        Thread {
            var success = 0
            for (filename in filenames) {
                try {
                    val response = ApiClient.create(this, preferences.getServerUrl())
                        .snapshotImage(cameraId, date, filename)
                        .execute()
                    if (response.isSuccessful) {
                        val bytes = response.body()?.bytes() ?: continue
                        saveToGallery(filename, bytes)
                        success++
                    }
                } catch (_: Exception) {}
            }
            val done = success
            runOnUiThread {
                statusText.text = "Saved $done / ${filenames.size} to gallery"
            }
        }.start()
    }

    private fun saveToGallery(filename: String, bytes: ByteArray) {
        val displayName = "${cameraId}_${date}_$filename"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, displayName)
                put(MediaStore.Images.Media.MIME_TYPE,    "image/jpeg")
                put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/SurvCam")
            }
            val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            uri?.let { contentResolver.openOutputStream(it)?.use { os -> os.write(bytes) } }
        } else {
            val dir = File(getExternalFilesDir(Environment.DIRECTORY_PICTURES), "SurvCam")
            dir.mkdirs()
            File(dir, displayName).writeBytes(bytes)
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    private fun formatHour(h: Int): String {
        fun label(n: Int) = when {
            n == 0  -> "12 AM"; n < 12 -> "$n AM"; n == 12 -> "12 PM"; else -> "${n-12} PM"
        }
        return "${label(h)} – ${label((h+1) % 24)}"
    }

    private fun formatDay(dateStr: String): String = try {
        SimpleDateFormat("yyyy-MM-dd  EEEE", Locale.US)
            .format(SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(dateStr)!!)
    } catch (_: Exception) { dateStr }
}
