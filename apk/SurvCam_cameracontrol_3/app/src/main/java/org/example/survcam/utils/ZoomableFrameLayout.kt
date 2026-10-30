package org.example.survcam.utils

import android.content.Context
import android.util.AttributeSet
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.widget.FrameLayout

/**
 * A FrameLayout that provides smooth, focal-point pinch-to-zoom and
 * drag-to-pan for any child view.
 *
 * Usage in the host:
 *   container.onZoomChanged = { scale, tx, ty ->
 *       childView.scaleX = scale
 *       childView.scaleY = scale
 *       childView.translationX = tx
 *       childView.translationY = ty
 *   }
 *
 * Single-finger events at scale == 1 are NOT intercepted, so parent
 * ViewPager2 / RecyclerView swipes still work normally at 1×.
 * When scale > 1, all touch events are intercepted for pan and the parent
 * is told not to intercept via requestDisallowInterceptTouchEvent.
 */
class ZoomableFrameLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : FrameLayout(context, attrs) {

    /**
     * Fired on every scale / translate change.
     *   scale — current zoom factor (1.0 = no zoom, max 6.0)
     *   tx    — horizontal translation in pixels (0 when fully zoomed out)
     *   ty    — vertical   translation in pixels (0 when fully zoomed out)
     */
    var onZoomChanged: ((scale: Float, tx: Float, ty: Float) -> Unit)? = null

    private var scaleFactor  = 1f
    private var tx           = 0f
    private var ty           = 0f
    private var intercepting = false

    // ── Scale gesture — focal-point zoom ────────────────────────────────────

    private val scaleDetector = ScaleGestureDetector(context,
        object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(d: ScaleGestureDetector): Boolean {
                val old      = scaleFactor
                val new      = (scaleFactor * d.scaleFactor).coerceIn(1f, 6f)
                val ratio    = new / old
                val pivotX   = width  / 2f
                val pivotY   = height / 2f

                // Keep the pixel under the pinch centre stationary on screen.
                // Derivation: screenX = (contentX − pivotX) × scale + pivotX + tx
                // Solve for tx_new so that screenX at focusX is unchanged.
                tx += (d.focusX - pivotX - tx) * (1f - ratio)
                ty += (d.focusY - pivotY - ty) * (1f - ratio)
                scaleFactor  = new

                if (scaleFactor <= 1f) { tx = 0f; ty = 0f }
                clamp()
                emit()
                return true
            }
        })

    // ── Pan gesture — drag while zoomed in ──────────────────────────────────

    private val gestureDetector = GestureDetector(context,
        object : GestureDetector.SimpleOnGestureListener() {
            override fun onScroll(
                e1: MotionEvent?, e2: MotionEvent,
                dx: Float, dy: Float          // positive dx = finger moved left
            ): Boolean {
                if (scaleFactor <= 1f) return false
                tx -= dx
                ty -= dy
                clamp()
                emit()
                return true
            }
        })

    // ── Touch interception ───────────────────────────────────────────────────

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {

            MotionEvent.ACTION_DOWN -> {
                // Already zoomed in — grab everything so we can handle panning.
                intercepting = scaleFactor > 1f
                if (intercepting) parent?.requestDisallowInterceptTouchEvent(true)
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                // Second finger arrived — take over for pinch-zoom.
                // Prime the detector with this event so it has both pointer positions.
                scaleDetector.onTouchEvent(ev)
                intercepting = true
                parent?.requestDisallowInterceptTouchEvent(true)
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                intercepting = false
                if (scaleFactor <= 1f) parent?.requestDisallowInterceptTouchEvent(false)
            }
        }
        return intercepting
    }

    override fun onTouchEvent(ev: MotionEvent): Boolean {
        scaleDetector.onTouchEvent(ev)
        if (!scaleDetector.isInProgress) gestureDetector.onTouchEvent(ev)

        if (ev.actionMasked == MotionEvent.ACTION_UP ||
            ev.actionMasked == MotionEvent.ACTION_CANCEL) {
            intercepting = false
            if (scaleFactor <= 1f) parent?.requestDisallowInterceptTouchEvent(false)
        }
        return true
    }

    // ── Public helpers ───────────────────────────────────────────────────────

    fun resetZoom() { scaleFactor = 1f; tx = 0f; ty = 0f; emit() }

    // ── Private ──────────────────────────────────────────────────────────────

    /** Prevent the content from scrolling beyond its edges when zoomed. */
    private fun clamp() {
        if (width == 0 || height == 0 || scaleFactor <= 1f) return
        val maxTx = width  / 2f * (scaleFactor - 1f)
        val maxTy = height / 2f * (scaleFactor - 1f)
        tx = tx.coerceIn(-maxTx, maxTx)
        ty = ty.coerceIn(-maxTy, maxTy)
    }

    private fun emit() = onZoomChanged?.invoke(scaleFactor, tx, ty)
}
