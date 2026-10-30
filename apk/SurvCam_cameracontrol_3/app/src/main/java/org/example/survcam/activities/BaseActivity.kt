package org.example.survcam.activities

import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Base activity that correctly handles edge-to-edge display (the SDK 35 default)
 * by combining the real system-bar insets with a fixed 24 dp safe margin on all
 * four sides.  Activities that manage their own insets (e.g. fullscreen video
 * screens) should extend AppCompatActivity directly instead of this class.
 */
abstract class BaseActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Opt the window into edge-to-edge drawing so we receive the real insets.
        WindowCompat.setDecorFitsSystemWindows(window, false)
    }

    override fun setContentView(layoutResID: Int) {
        super.setContentView(layoutResID)
        applySafeInsets()
    }

    private fun applySafeInsets() {
        val content = window.decorView.findViewById<ViewGroup>(android.R.id.content)
        val root = content.getChildAt(0) ?: return
        // Only left/right need a screen-protector safe margin.
        // Top/bottom are already handled correctly by the system bar insets alone.
        val safeLR = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(root) { v: View, insets: WindowInsetsCompat ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(
                bars.left  + safeLR,
                bars.top,
                bars.right + safeLR,
                bars.bottom
            )
            insets
        }
    }
}
