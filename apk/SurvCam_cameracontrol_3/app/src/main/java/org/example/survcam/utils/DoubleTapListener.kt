package org.example.survcam.utils

import android.content.Context
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.View

class DoubleTapListener(context: Context, private val onDoubleTap: () -> Unit) : View.OnTouchListener {
    private val detector = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDoubleTap(e: MotionEvent): Boolean {
            onDoubleTap()
            return true
        }
    })

    override fun onTouch(v: View, event: MotionEvent): Boolean {
        detector.onTouchEvent(event)
        return true
    }
}
