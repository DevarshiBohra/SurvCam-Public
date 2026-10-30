package org.example.survcam.activities

import android.content.ContentValues
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.example.survcam.R
import org.example.survcam.api.ApiClient
import org.example.survcam.storage.AppPreferences
import org.example.survcam.utils.Constants
import org.example.survcam.utils.ZoomableFrameLayout
import java.io.File

class SnapshotViewerActivity : AppCompatActivity() {

    private lateinit var imageView:   ImageView
    private lateinit var loadingBar:  ProgressBar
    private lateinit var topBar:      LinearLayout
    private lateinit var preferences: AppPreferences
    private lateinit var cameraId:    String
    private lateinit var date:        String
    private lateinit var filename:    String
    private var imageBytes: ByteArray? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_snapshot_viewer)

        cameraId    = intent.getStringExtra(Constants.EXTRA_CAMERA_ID).orEmpty()
        date        = intent.getStringExtra(Constants.EXTRA_DATE).orEmpty()
        filename    = intent.getStringExtra(Constants.EXTRA_FILENAME).orEmpty()
        preferences = AppPreferences(this)

        imageView  = findViewById(R.id.snapshotImage)
        loadingBar = findViewById(R.id.loadingBar)
        topBar     = findViewById(R.id.topBar)

        // Push top bar content below the status bar
        val safeLR = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(topBar) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left + safeLR, bars.top, bars.right + safeLR, 0)
            insets
        }

        // Show time as title (e.g. "18:38:05")
        val timeLabel = filename.removeSuffix(".jpg").replace("-", ":")
        findViewById<TextView>(R.id.titleText).text = "$date  ·  $timeLabel"

        // Pinch-to-zoom via existing ZoomableFrameLayout
        val zoomLayout = findViewById<ZoomableFrameLayout>(R.id.zoomLayout)
        zoomLayout.onZoomChanged = { scale, tx, ty ->
            imageView.scaleX       = scale
            imageView.scaleY       = scale
            imageView.translationX = tx
            imageView.translationY = ty
        }

        findViewById<Button>(R.id.downloadButton).setOnClickListener { downloadCurrent() }

        loadImage()
    }

    // ── Image loading ──────────────────────────────────────────────────────────

    private fun loadImage() {
        Thread {
            try {
                val response = ApiClient.create(this, preferences.getServerUrl())
                    .snapshotImage(cameraId, date, filename)
                    .execute()
                if (response.isSuccessful) {
                    val bytes  = response.body()?.bytes() ?: return@Thread
                    imageBytes = bytes
                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    runOnUiThread {
                        imageView.setImageBitmap(bitmap)
                        loadingBar.visibility = View.GONE
                    }
                } else {
                    runOnUiThread { showError("Could not load image (${response.code()})") }
                }
            } catch (e: Exception) {
                runOnUiThread { showError("Network error: ${e.localizedMessage}") }
            }
        }.start()
    }

    private fun showError(msg: String) {
        loadingBar.visibility = View.GONE
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
    }

    // ── Single-image download ──────────────────────────────────────────────────

    private fun downloadCurrent() {
        val bytes = imageBytes
        if (bytes == null) {
            Toast.makeText(this, "Image not yet loaded", Toast.LENGTH_SHORT).show()
            return
        }
        Thread {
            try {
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
                runOnUiThread { Toast.makeText(this, "Saved to gallery", Toast.LENGTH_SHORT).show() }
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this, "Download failed", Toast.LENGTH_SHORT).show() }
            }
        }.start()
    }
}
