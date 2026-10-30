package org.example.survcam.adapter

import androidx.fragment.app.FragmentActivity
import androidx.viewpager2.adapter.FragmentStateAdapter
import org.example.survcam.activities.MultiCameraFragment
import org.example.survcam.models.Camera

/**
 * Feeds pages of cameras into ViewPager2.
 *
 * Stable item IDs are derived from the camera IDs on each page so that when
 * the 30-second auto-refresh rebuilds the adapter, fragments whose cameras
 * haven't changed are reused rather than destroyed and recreated (which would
 * briefly interrupt the MJPEG streams).
 */
class MultiCameraPageAdapter(
    fa: FragmentActivity,
    private val pages: List<List<Camera>>
) : FragmentStateAdapter(fa) {

    override fun getItemCount(): Int = pages.size

    override fun createFragment(position: Int) =
        MultiCameraFragment.newInstance(pages[position])

    /** Stable ID = hash of the ordered camera-ID list on that page. */
    override fun getItemId(position: Int): Long =
        pages[position].joinToString(",") { it.cameraId }.hashCode().toLong()

    override fun containsItem(itemId: Long): Boolean =
        pages.any { page ->
            page.joinToString(",") { it.cameraId }.hashCode().toLong() == itemId
        }
}
